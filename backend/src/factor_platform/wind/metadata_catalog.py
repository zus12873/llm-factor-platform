"""Localized Wind field metadata: what a field *means*, held offline.

Two catalogs sit side by side. :mod:`factor_platform.wind.catalog` answers "does
this table.field exist"; this one answers "what is it, in what unit, observed how
often, and which column carries its date". Together they let field discovery
filter by asset type and frequency instead of ranking on field-name lexemes
alone.

Nothing here touches the network. The metadata is built once from the local Wind
data dictionary and reloaded from JSONL at runtime.

``metadata_source is None`` is a first-class state, not an error: the dictionary
is a PDF extraction and a large minority of its rows are damaged beyond repair.
Those fields still exist and must still be findable — they simply rank without
the benefit of a description.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from pydantic import BaseModel

from factor_platform.domain.models import AssetType, Frequency


class FieldMetadata(BaseModel):
    """Everything known about one Wind ``table.field``, from local sources only."""

    schema_version: int = 1
    table: str
    field: str

    name_zh: str = ""
    description_zh: str = ""
    data_type: str | None = None
    fill_rate: float | None = None
    unit: str | None = None

    asset_type: AssetType | None = None
    frequency: Frequency | None = None
    # Which market the table covers (``cn_a``, ``hk``, ...). A-share and HK stock
    # tables are both ``asset_type=stock`` with identical Chinese field names, so
    # this is the only thing separating them.
    market: str | None = None

    # Which column in the same table carries each kind of date. These are what
    # make point-in-time queries expressible without hard-coding table names.
    security_code_field: str | None = None
    observation_date_field: str | None = None
    announcement_date_field: str | None = None
    report_period_field: str | None = None

    business_keys: list[str] = []
    is_derived: bool = False
    note: str = ""

    # ``None`` means "no metadata was recoverable", never "not applicable".
    metadata_source: str | None = None
    metadata_version: int = 1


def _key(table: str, field: str) -> tuple[str, str]:
    return table.strip().lower(), field.strip().lower()


class MetadataCatalog:
    """Indexed, JSONL-backed collection of :class:`FieldMetadata`."""

    def __init__(self, records: Iterable[FieldMetadata]) -> None:
        self.records: list[FieldMetadata] = list(records)
        self._by_key: dict[tuple[str, str], FieldMetadata] = {
            _key(record.table, record.field): record for record in self.records
        }

    def get(self, table: str, field: str) -> FieldMetadata | None:
        """Return metadata for ``table.field``, or ``None`` when unknown."""
        return self._by_key.get(_key(table, field))

    @classmethod
    def load(cls, path: Path | str) -> MetadataCatalog:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        return cls(
            FieldMetadata.model_validate_json(line) for line in lines if line.strip()
        )

    def save(self, path: Path | str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "".join(
                f"{json.dumps(record.model_dump(mode='json'), ensure_ascii=False)}\n"
                for record in self.records
            ),
            encoding="utf-8",
        )

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[FieldMetadata]:
        return iter(self.records)


__all__ = ["FieldMetadata", "MetadataCatalog"]
