"""Wind field catalog: parse the Markdown field index into normalized records.

The source file ``windquery/windquery/references/wind_field_index.md`` lists all
~7,480 Wind fields grouped under ~678 tables. Each table looks like::

    ### <TableName>（N个字段）

    FIELD_A, FIELD_B, FIELD_C

This module parses that file into ``FieldRecord`` rows (table+field lowercased),
persists them as JSONL, and reloads them for the search layer. Parsing is fully
offline — no Wind DB connection, no credentials.

The parser is defensive: blank tokens (trailing commas, blank lines, stub fields
such as ``EST_``) are skipped rather than crashing, because the real index has a
handful of irregularities that should not abort the catalog build.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_TABLE_HEADER_RE = re.compile(r"^###\s+([A-Za-z0-9_]+)\s*[（(]")


@dataclass(frozen=True)
class FieldRecord:
    """One normalized Wind field.

    Both ``table`` and ``field`` are lowercased at build time so that downstream
    search and alias matching can compare against canonical lowercase keys.
    """

    table: str
    field: str


class CatalogBuilder:
    """Build a list of ``FieldRecord`` rows from the Wind Markdown field index.

    The builder is stateless beyond the source path: ``build()`` reads the file,
    walks line by line, and emits one record per non-empty field token under the
    most recently seen ``### <Table>（...）`` header. Doc titles, blockquote
    intros, and ``##`` category headers are ignored.
    """

    def __init__(self, source: Path | str) -> None:
        self.source = Path(source)

    def build(self) -> list[FieldRecord]:
        text = self.source.read_text(encoding="utf-8")
        records: list[FieldRecord] = []
        current_table: str | None = None

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("### "):
                current_table = self._parse_table_header(line)
                continue

            # Document title, category headers (##), and blockquote intros (>)
            # are never field lines.
            if line.startswith("#") or line.startswith(">"):
                continue

            if current_table is None:
                continue

            for token in line.split(","):
                field = token.strip().lower()
                if field:
                    records.append(FieldRecord(table=current_table, field=field))

        return records

    @staticmethod
    def _parse_table_header(line: str) -> str | None:
        match = _TABLE_HEADER_RE.match(line)
        if not match:
            return None
        return match.group(1).strip().lower()


class FieldCatalog:
    """In-memory catalog of ``FieldRecord`` rows with JSONL persistence."""

    def __init__(self, records: list[FieldRecord]) -> None:
        self.records: list[FieldRecord] = list(records)

    @classmethod
    def load(cls, path: Path | str) -> FieldCatalog:
        """Load a catalog from a JSONL file produced by :meth:`save` or the CLI.

        Each non-blank line is a JSON object ``{"table": ..., "field": ...}``.
        Blank lines are skipped so the file remains diff-friendly.
        """
        path = Path(path)
        records: list[FieldRecord] = []
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            obj = json.loads(line)
            records.append(
                FieldRecord(table=obj["table"].lower(), field=obj["field"].lower())
            )
        return cls(records)

    def save(self, path: Path | str) -> None:
        """Write the catalog as JSONL, creating parent directories as needed."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for record in self.records:
                payload = {"table": record.table, "field": record.field}
                fh.write(json.dumps(payload, ensure_ascii=False))
                fh.write("\n")

    def __len__(self) -> int:
        return len(self.records)


__all__ = ["CatalogBuilder", "FieldCatalog", "FieldRecord"]
