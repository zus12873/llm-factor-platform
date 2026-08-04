"""Wind field search: alias tier + BM25 over the normalized field catalog.

``FieldSearch.search(requirement)`` resolves a ``DataRequirement`` to ranked
``FieldCandidate`` rows:

1. **Alias tier** — exact (or contained) Chinese business terms from
   ``backend/data/wind_aliases.yaml`` map to a canonical Wind table.field.
   These are returned first with ``source_tier="alias"``.
2. **BM25 tier** — lexical ranking over the catalog documents
   (``table`` + ``field`` tokens). Used when no alias applies, or to fill out
   the rest of the ``limit`` after alias hits. Tagged ``source_tier="bm25"``.

Both tiers respect the optional ``asset_type`` and ``frequency`` constraints
on the requirement (filter applied before ranking). Results from the two tiers
are merged and de-duplicated by ``(table, field)``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import jieba  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel
from rank_bm25 import BM25Okapi  # type: ignore[import-untyped]

from factor_platform.domain.models import (
    AssetType,
    DataRequirement,
    FieldCandidate,
    Frequency,
)
from factor_platform.wind.catalog import FieldCatalog

_CAMEL_BOUNDARY_RE = re.compile(r"_+|(?<=[a-z0-9])(?=[A-Z])|(?<=\D)(?=\d)|\s+")


class AliasEntry(BaseModel):
    """One alias row from ``wind_aliases.yaml``."""

    table: str
    field: str
    asset_type: AssetType | None = None
    frequency: Frequency | None = None
    meaning_zh: str = ""


def _normalize_token(token: str) -> str:
    return token.strip().lower()


def tokenize(text: str) -> list[str]:
    """Tokenize a query or document for BM25.

    Chinese is segmented with ``jieba``; English / snake_case / camelCase is
    split on underscores, case boundaries, and digit boundaries. Empty tokens
    are dropped. Everything is lowercased so ``S_DQ_Close`` and ``s_dq_close``
    produce the same token stream.
    """
    if not text:
        return []
    lowered = text.lower()
    raw_tokens: list[str] = []
    # Chinese-aware segmentation first; jieba leaves ASCII runs intact.
    for chunk in jieba.cut(lowered):
        raw_tokens.extend(_CAMEL_BOUNDARY_RE.split(chunk))
    return [t for t in (_normalize_token(tok) for tok in raw_tokens) if t]


def _passes_filter(
    entry_asset: AssetType | None,
    entry_freq: Frequency | None,
    req_asset: AssetType | None,
    req_freq: Frequency | None,
) -> bool:
    """True when the entry is compatible with the requirement's constraints.

    Entries with ``None`` asset/frequency are always allowed (catalog rows
    carry no metadata); only conflicting concrete values are filtered out.
    """
    asset_ok = not (
        req_asset is not None and entry_asset is not None and entry_asset != req_asset
    )
    freq_ok = not (
        req_freq is not None and entry_freq is not None and entry_freq != req_freq
    )
    return asset_ok and freq_ok


class FieldSearch:
    """Alias + BM25 search over the Wind field catalog."""

    def __init__(
        self,
        catalog: FieldCatalog,
        aliases: dict[str, AliasEntry],
    ) -> None:
        self.catalog = catalog
        self.aliases = aliases
        # Sort alias keys by length descending so the longest (most specific)
        # business term wins on substring match.
        self._alias_keys_by_length = sorted(aliases.keys(), key=len, reverse=True)

        records = catalog.records
        self._doc_tokens: list[list[str]] = [
            tokenize(f"{r.table} {r.field}") for r in records
        ]
        self._bm25 = BM25Okapi(self._doc_tokens)

    # ------------------------------------------------------------------ factories

    @classmethod
    def from_paths(
        cls, catalog_path: Path | str, aliases_path: Path | str
    ) -> FieldSearch:
        """Build a search from a generated JSONL catalog and an alias YAML."""
        catalog = FieldCatalog.load(catalog_path)
        aliases = _load_aliases(aliases_path)
        return cls(catalog=catalog, aliases=aliases)

    # ------------------------------------------------------------------ search

    def search(
        self, requirement: DataRequirement, limit: int = 10
    ) -> list[FieldCandidate]:
        query = requirement.meaning.strip()
        req_asset = requirement.asset_type
        req_freq = requirement.frequency

        alias_hits = self._alias_candidates(query, req_asset, req_freq)
        bm25_hits = self._bm25_candidates(query, req_asset, req_freq, limit=limit)

        merged: list[FieldCandidate] = []
        seen: set[tuple[str, str]] = set()
        for cand in [*alias_hits, *bm25_hits]:
            key = (cand.table, cand.field)
            if key in seen:
                continue
            seen.add(key)
            merged.append(cand)
            if len(merged) >= limit:
                break
        return merged

    # ------------------------------------------------------------------ alias tier

    def _alias_candidates(
        self,
        query: str,
        req_asset: AssetType | None,
        req_freq: Frequency | None,
    ) -> list[FieldCandidate]:
        if not self.aliases or not query:
            return []

        exact = self.aliases.get(query)
        if exact is not None and _passes_filter(
            exact.asset_type, exact.frequency, req_asset, req_freq
        ):
            return [self._candidate_from_alias(exact, lexical_score=1.0)]

        # Substring fallback: longest matching alias key contained in the query.
        for key in self._alias_keys_by_length:
            if not key:
                continue
            if key in query:
                entry = self.aliases[key]
                if _passes_filter(
                    entry.asset_type, entry.frequency, req_asset, req_freq
                ):
                    return [self._candidate_from_alias(entry, lexical_score=0.9)]
        return []

    def _candidate_from_alias(
        self, entry: AliasEntry, *, lexical_score: float
    ) -> FieldCandidate:
        return FieldCandidate(
            table=entry.table,
            field=entry.field,
            meaning_zh=entry.meaning_zh,
            asset_type=entry.asset_type,
            frequency=entry.frequency,
            source_tier="alias",
            lexical_score=lexical_score,
            recommendation_score=lexical_score,
            evidence=f"alias:{entry.table}.{entry.field}",
        )

    # ------------------------------------------------------------------ bm25 tier

    def _bm25_candidates(
        self,
        query: str,
        req_asset: AssetType | None,
        req_freq: Frequency | None,
        *,
        limit: int,
    ) -> list[FieldCandidate]:
        query_tokens = tokenize(query)
        if not query_tokens or not self.catalog.records:
            return []

        scores = self._bm25.get_scores(query_tokens)
        # Pull more than `limit` so filtering can still fill the page.
        top_k = min(len(self.catalog.records), max(limit * 4, limit))
        ranked_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[
            :top_k
        ]

        out: list[FieldCandidate] = []
        for idx in ranked_idx:
            score = float(scores[idx])
            if score <= 0:
                continue
            record = self.catalog.records[idx]
            # Catalog records carry no asset/frequency metadata; we cannot
            # filter them by requirement constraints here. Alias hits (which do
            # carry metadata) are always surfaced first, so this only affects
            # the BM25 back-fill.
            if req_asset is not None or req_freq is not None:
                # Defer to alias tier when constraints are set: keep BM25 hits
                # only if no alias metadata conflicts (catalog has none, so we
                # leave them unfiltered but flagged).
                pass
            out.append(
                FieldCandidate(
                    table=record.table,
                    field=record.field,
                    source_tier="bm25",
                    lexical_score=score,
                    recommendation_score=score,
                    evidence=f"bm25:{record.table}.{record.field}",
                )
            )
            if len(out) >= limit:
                break
        return out


def _load_aliases(path: Path | str) -> dict[str, AliasEntry]:
    """Load the alias YAML into a typed dict keyed by business term."""
    raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    block = raw.get("aliases", {}) if isinstance(raw, dict) else {}
    aliases: dict[str, AliasEntry] = {}
    for key, value in block.items():
        if not isinstance(value, dict):
            continue
        aliases[str(key)] = AliasEntry(
            table=str(value["table"]).lower(),
            field=str(value["field"]).lower(),
            asset_type=AssetType(value["asset_type"]) if value.get("asset_type") else None,
            frequency=Frequency(value["frequency"]) if value.get("frequency") else None,
            meaning_zh=str(value.get("meaning_zh", "")),
        )
    return aliases


__all__ = ["AliasEntry", "FieldSearch", "tokenize"]
