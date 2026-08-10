"""Merge the field index with dictionary metadata into one lookup.

Two sources, both damaged. The Markdown **field index** lists ``table.field``
pairs; the **data dictionary** describes what they mean. Both were extracted from
Wind's PDFs, and each lost rows the other kept — measured on the shipped copies,
the index is missing 74 fields the dictionary has (including
``ashareeodderivativeindicator.s_val_mv``, the market-cap column) and the
dictionary is missing 478 the index has.

So the merge is a **union**, not an index-driven left join. Treating either
source as the authority on existence would delete real fields, and the deletion
would be invisible: a user searching for market cap would simply get nothing back
and conclude Wind does not have it.

This also keeps the discovery funnel's division of labour intact. The first four
layers recall — they should over-include. Existence is settled later, by
``information_schema`` and sample verification, against the live database rather
than against a PDF extraction. A field that neither source got right is a field
the funnel never sees; a field only one source kept is one the falsification
layers can still confirm or reject.

Fields the dictionary could not describe are emitted with
``metadata_source=None`` rather than dropped, for the same reason.
"""

from __future__ import annotations

from collections.abc import Iterable

from factor_platform.wind.catalog import FieldRecord
from factor_platform.wind.metadata_catalog import FieldMetadata


class MetadataRepository:
    """Combines index records and dictionary metadata into a versioned mapping."""

    @staticmethod
    def merge(
        index_records: Iterable[FieldRecord],
        wds_records: Iterable[FieldMetadata],
        *,
        version: int = 1,
    ) -> dict[tuple[str, str], FieldMetadata]:
        """Return the union of both sources, described wherever the dictionary reached.

        WDS wins on overlap. Index-only fields survive as bare records marked
        ``metadata_source=None``; dictionary-only fields survive with their
        metadata intact.
        """
        described = {
            (record.table.lower(), record.field.lower()): record for record in wds_records
        }

        merged: dict[tuple[str, str], FieldMetadata] = {
            key: record.model_copy(update={"metadata_version": version})
            for key, record in described.items()
        }
        for record in index_records:
            key = (record.table.lower(), record.field.lower())
            if key in merged:
                continue
            merged[key] = FieldMetadata(
                table=key[0],
                field=key[1],
                metadata_source=None,
                metadata_version=version,
            )
        return merged

    @staticmethod
    def coverage(merged: dict[tuple[str, str], FieldMetadata]) -> float:
        """Fraction of fields the dictionary could describe, for the sync report."""
        if not merged:
            return 0.0
        described = sum(1 for m in merged.values() if m.metadata_source is not None)
        return described / len(merged)


__all__ = ["MetadataRepository"]
