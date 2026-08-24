"""Parse the local Wind data dictionary into :class:`FieldMetadata`.

The dictionaries are Markdown extracted from Wind's PDF documentation, one file
per table, each a header block plus a field table. The extraction is lossy: cell
boundaries drift, whole rows collapse into a neighbour's last cell, and stray
watermark text lands in description columns. Roughly 40% of declared fields
cannot be recovered cleanly.

Two rules follow from that, and they point in opposite directions on purpose:

* **Recover what is unambiguous.** A collapsed row still contains
  ``<中文名> <FIELD_NAME> <TYPE>(...)`` in order. That pattern is specific enough
  to pull the field back out, and it rescues real fields — ``S_VAL_MV``, the
  market-cap column, is folded this way in the shipped dictionary.
* **Skip everything else.** A half-parsed row would yield a field with the wrong
  Chinese name or the wrong type, and nothing downstream re-checks it. Missing
  metadata degrades ranking visibly; wrong metadata misleads silently.

Frequency is derived from the declared **business key**, not the table name. The
business key is the observation grain: keyed by 交易日期 means one row per
trading day, keyed by 报告期 means one row per reporting period. Table names only
look reliable until ``AShareEODDerivativeIndicator`` meets ``AShareIncome``.

Units are not in the dictionary at all. They come from a small reviewable overlay
and default to ``None`` — see :mod:`factor_platform.wind.metadata_catalog`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final, NamedTuple

import yaml  # type: ignore[import-untyped]

from factor_platform.domain.models import AssetType, Frequency
from factor_platform.wind.metadata_catalog import FieldMetadata

_MODULE: Final = re.compile(r"\*\*所属模块\*\*:\s*(.+)")
_NOTE: Final = re.compile(r"\*\*注释说明\*\*:\s*(.+)")
_BUSINESS_KEY: Final = re.compile(r"业务主键：\s*(.+?)(?:\s+所属数据库|$)")
_SOURCE_DB: Final = re.compile(r"所属数据库：\s*(\S+)")
_TABLE_NAME: Final = re.compile(r"^#\s*(.+?)_数据字典\s*$", re.M)

_FIELD_NAME: Final = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
_TYPE: Final = re.compile(r"^(?:VARCHAR2|NUMBER|DATE|CHAR|CLOB|FLOAT|INTEGER)\b", re.I)
_FILL_RATE: Final = re.compile(r"^(\d+(?:\.\d+)?)%$")
_TYPE_VALUE: Final = re.compile(
    r"(?:VARCHAR2|NUMBER|CHAR)\s*\([\d\s,]+\)|\b(?:DATE|CLOB|FLOAT|INTEGER)\b",
    re.I,
)
_FILL_RATE_VALUE: Final = re.compile(r"(\d+(?:\.\d+)?)%")
_FIELD_AND_TYPE: Final = re.compile(
    r"\b([A-Z][A-Z0-9_]{2,})\s+"
    r"((?:VARCHAR2|NUMBER|CHAR)\s*\([\d\s,]+\)|(?:DATE|CLOB|FLOAT|INTEGER))"
    r"(?=$|\s)",
    re.I,
)

# A row that collapsed into a neighbouring cell still keeps its parts in order.
_FOLDED: Final = re.compile(
    r"([一-鿿（）()A-Za-z0-9:：\-\s]{2,20}?)\s*"
    r"\b([A-Z][A-Z0-9_]{2,})\s+"
    r"((?:VARCHAR2|NUMBER|DATE|CHAR|CLOB|FLOAT|INTEGER)\s*\([\d\s,]+\)|"
    r"(?:DATE|CLOB))"
)

# 所属模块 -> asset type. Absent or unrecognised leaves the asset type unset.
_MODULE_ASSET: Final[dict[str, AssetType]] = {
    "中国A股-股票信息": AssetType.STOCK,
    "中国A股-指数数据": AssetType.INDEX,
    "中国基金-基金信息": AssetType.FUND,
    "中国债券-债券信息": AssetType.BOND,
    "中国债券-资产支持证券": AssetType.BOND,
    "期货-股指期货": AssetType.FUTURES,
}

# 所属数据库 substring -> market code. Checked in order; the first hit wins.
# Only the market matters here, not the asset class: 中国香港股票数据 and
# 中国A股数据库 are both stock tables with the same Chinese field names, and the
# platform's scope covers only the latter.
_SOURCE_DB_MARKET: Final[tuple[tuple[str, str], ...]] = (
    ("香港", "hk"),
    ("A股", "cn_a"),
    ("债券", "cn_bond"),
    ("基金", "cn_fund"),
    ("期货", "cn_futures"),
    ("美股", "us"),
)

# Business-key token -> observation grain, most specific first.
_KEY_FREQUENCY: Final[tuple[tuple[str, Frequency], ...]] = (
    ("交易日期", Frequency.DAILY),
    ("交易日", Frequency.DAILY),
    ("报告期", Frequency.QUARTERLY),
    ("截止日期", Frequency.QUARTERLY),
    ("日期", Frequency.DAILY),
)

# Field name -> the date role it plays for every other field in its table.
_DATE_ROLES: Final[dict[str, str]] = {
    "trade_dt": "observation_date_field",
    "price_date": "observation_date_field",
    "ann_dt": "announcement_date_field",
    "ann_date": "announcement_date_field",
    "report_period": "report_period_field",
}
_CODE_FIELDS: Final[tuple[str, ...]] = (
    "s_info_windcode",
    "f_info_windcode",
    "b_info_windcode",
    "wind_code",
)


class DictionaryBuilder:
    """Builds :class:`FieldMetadata` from a directory of dictionary Markdown."""

    def __init__(self, root: Path | str, *, units_path: Path | str | None = None) -> None:
        self._root = Path(root)
        self._units = _load_units(units_path) if units_path else {}

    def build(self) -> list[FieldMetadata]:
        """Parse every ``*_数据字典.md`` under the root, in sorted path order."""
        records: list[FieldMetadata] = []
        sources = [self._root] if self._root.is_file() else sorted(self._root.rglob("*.md"))
        for path in sources:
            records.extend(self._build_one(path))
        return records

    # ------------------------------------------------------------------ per file

    def _build_one(self, path: Path) -> list[FieldMetadata]:
        text = path.read_text(encoding="utf-8", errors="replace")

        table_match = _TABLE_NAME.search(text)
        table = (table_match.group(1) if table_match else path.stem.split("_")[0]).lower()

        module_match = _MODULE.search(text)
        asset_type = _MODULE_ASSET.get(module_match.group(1).strip()) if module_match else None

        note_match = _NOTE.search(text)
        note = note_match.group(1).strip() if note_match else ""
        business_keys = _parse_business_keys(note)
        frequency = _frequency_from_keys(business_keys)
        market = _market_from_note(note)

        rows = [self._parse_row(line) for line in text.splitlines() if line.startswith("|")]
        parsed = [row for row in rows if row is not None]
        if not parsed:
            return []

        field_names = {name for name, _, _, _, _ in parsed}
        roles = _date_roles(field_names)

        return [
            FieldMetadata(
                table=table,
                field=name,
                name_zh=name_zh,
                description_zh=description,
                data_type=data_type,
                fill_rate=fill_rate,
                unit=self._units.get(f"{table}.{name}"),
                asset_type=asset_type,
                frequency=frequency,
                market=market,
                business_keys=business_keys,
                note=note,
                metadata_source="WDS",
                security_code_field=roles.security_code_field,
                observation_date_field=roles.observation_date_field,
                announcement_date_field=roles.announcement_date_field,
                report_period_field=roles.report_period_field,
            )
            for name, name_zh, data_type, fill_rate, description in parsed
        ]

    # ------------------------------------------------------------------ per row

    def _parse_row(
        self, line: str
    ) -> tuple[str, str, str | None, float | None, str] | None:
        """Return ``(field, name_zh, data_type, fill_rate, description)`` or ``None``.

        ``None`` means the row could not be read with confidence. It is dropped
        rather than partially trusted.
        """
        if line.startswith("|---") or "序号" in line:
            return None
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            return None

        # PDF-to-Markdown extraction sometimes inserts pipes inside a Chinese
        # display name, shifting the field from column 3 to column 4. Locate the
        # first unambiguous Wind identifier instead of assuming a fixed column.
        field_index = next(
            (index for index, cell in enumerate(cells[2:], start=2) if _FIELD_NAME.match(cell)),
            None,
        )
        embedded_type: str | None = None
        embedded_name: str | None = None
        if field_index is None:
            # A damaged row may contain an earlier, unrelated ``FIELD TYPE``
            # pair and then the real row folded into a later cell. The last
            # recoverable pair is the one belonging to the collapsed row.
            combined_candidates = [
                (index, match, cell)
                for index, cell in enumerate(cells[2:], start=2)
                if (match := _FIELD_AND_TYPE.search(cell)) is not None
            ]
            combined = combined_candidates[-1] if combined_candidates else None
            if combined is not None:
                field_index, match, cell = combined
                field_cell = match.group(1)
                embedded_type = re.sub(r"\s+", "", match.group(2))
                embedded_name = cell[: match.start()].strip()
            else:
                field_cell = ""
        else:
            field_cell = cells[field_index]

        if field_index is not None:
            name_zh = embedded_name or " ".join(cells[1:field_index]).strip()
            rest = cells[field_index + 1 :]
            if embedded_type is not None:
                rest = [embedded_type, *rest]
            type_match = next(
                (match for cell in rest if (match := _TYPE_VALUE.search(cell)) is not None),
                None,
            )
            data_type = (
                re.sub(r"\s+", "", type_match.group(0)) if type_match is not None else None
            )
            fill_rate = next(
                (
                    float(m.group(1)) / 100
                    for c in rest
                    if (m := _FILL_RATE_VALUE.search(c)) is not None
                ),
                None,
            )
            description = ""
            for cell in reversed(rest):
                residual = _TYPE_VALUE.sub("", cell)
                residual = _FILL_RATE_VALUE.sub("", residual).strip()
                if residual:
                    description = residual
                    break
            return field_cell.lower(), name_zh, data_type, fill_rate, description

        # Field name and type collapsed into one cell, or the whole row folded
        # into a neighbour's trailing cell.
        recovered = _FOLDED.search(line)
        if recovered is not None:
            return (
                recovered.group(2).lower(),
                recovered.group(1).strip(),
                re.sub(r"\s+", "", recovered.group(3)),
                None,
                "",
            )
        return None


# --------------------------------------------------------------------------- helpers


def _parse_business_keys(note: str) -> list[str]:
    match = _BUSINESS_KEY.search(note)
    if match is None:
        return []
    return [part.strip() for part in match.group(1).split(",") if part.strip()]


def _market_from_note(note: str) -> str | None:
    """Read the market from 所属数据库, or leave it unset when undeclared."""
    match = _SOURCE_DB.search(note)
    if match is None:
        return None
    source = match.group(1)
    return next((code for token, code in _SOURCE_DB_MARKET if token in source), None)


def _frequency_from_keys(business_keys: list[str]) -> Frequency | None:
    """Map the declared business key to an observation grain, or leave it unset."""
    joined = " ".join(business_keys)
    for token, frequency in _KEY_FREQUENCY:
        if token in joined:
            return frequency
    return None


class _TableRoles(NamedTuple):
    """Which column in a table plays each date/identity role."""

    observation_date_field: str | None = None
    announcement_date_field: str | None = None
    report_period_field: str | None = None
    security_code_field: str | None = None


def _date_roles(field_names: set[str]) -> _TableRoles:
    found: dict[str, str | None] = {}
    for name, role in _DATE_ROLES.items():
        if name in field_names and found.get(role) is None:
            found[role] = name
    code_field = next((name for name in _CODE_FIELDS if name in field_names), None)
    return _TableRoles(
        observation_date_field=found.get("observation_date_field"),
        announcement_date_field=found.get("announcement_date_field"),
        report_period_field=found.get("report_period_field"),
        security_code_field=code_field,
    )


def _load_units(path: Path | str) -> dict[str, str]:
    """Load the hand-curated unit overlay; entries await Task 10.5 sign-off."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    block = raw.get("units", {}) if isinstance(raw, dict) else {}
    return {
        str(key).lower(): str(value["unit"])
        for key, value in block.items()
        if isinstance(value, dict) and value.get("unit")
    }


__all__ = ["DictionaryBuilder"]
