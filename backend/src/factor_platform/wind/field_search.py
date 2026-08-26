"""Wind field search: alias tier + BM25 over the normalized field catalog.

``FieldSearch.search(requirement)`` resolves a ``DataRequirement`` to ranked
``FieldCandidate`` rows:

1. **Alias tier** — exact (or contained) Chinese business terms from
   ``backend/data/wind_aliases.yaml`` map to a canonical Wind table.field.
   These are returned first with ``source_tier="alias"``.
2. **BM25 tier** — lexical ranking over the catalog documents
   (``table`` + ``field`` tokens). Used when no alias applies, or to fill out
   the rest of the ``limit`` after alias hits. Tagged ``source_tier="bm25"``.

3. **Metadata tier** — the local Wind dictionary supplies asset type, frequency,
   unit and the Chinese name for whichever fields it could describe. This is what
   makes the ``asset_type``/``frequency`` constraints real: before it existed the
   catalog carried no metadata, so those filters passed everything through.

A field the dictionary could not describe is **kept and flagged**
(``metadata_source=None``), never filtered out. Filtering on absent metadata
would quietly shrink the searchable universe, and the user would have no way to
tell a field excluded by a parsing artifact from one the database does not have.

4. **Price-semantic tier** — close/return/volatility queries may *boost*
   ``s_dq_adjclose`` and label unadjusted close. This is ranking, not an alias
   rewrite: ``收盘价`` still aliases to ``s_dq_close``, both rows stay visible,
   and the user still confirms a binding.

Results from the tiers are merged and de-duplicated by ``(table, field)``.
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
from factor_platform.wind.catalog import FieldCatalog, FieldRecord
from factor_platform.wind.metadata_catalog import MetadataCatalog
from factor_platform.wind.price_semantics import (
    PRICE_ADJUSTMENT_BY_FIELD,
    apply_price_semantics,
)

_CAMEL_BOUNDARY_RE = re.compile(r"_+|(?<=[a-z0-9])(?=[A-Z])|(?<=\D)(?=\d)|\s+")

# The platform's scope is daily A-shares. Off-market fields are demoted rather
# than removed: this is a recall layer, and `information_schema` plus sample
# verification are what decide whether a field is really usable.
PREFERRED_MARKET = "cn_a"


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
    asset_ok = not (req_asset is not None and entry_asset is not None and entry_asset != req_asset)
    freq_ok = not (req_freq is not None and entry_freq is not None and entry_freq != req_freq)
    return asset_ok and freq_ok


class FieldSearch:
    """Alias + BM25 search over the Wind field catalog."""

    def __init__(
        self,
        catalog: FieldCatalog,
        aliases: dict[str, AliasEntry],
        metadata: MetadataCatalog | None = None,
        *,
        preferred_market: str | None = PREFERRED_MARKET,
    ) -> None:
        self.catalog = catalog
        self.aliases = aliases
        self.metadata = metadata
        self.preferred_market = preferred_market
        # Sort alias keys by length descending so the longest (most specific)
        # business term wins on substring match.
        self._alias_keys_by_length = sorted(aliases.keys(), key=len, reverse=True)

        # Documents carry the Chinese name and description when metadata has
        # them. Indexing only the English identifiers would leave the 32
        # hand-written aliases as the sole Chinese entry point into the whole
        # catalog, which is the language every research idea arrives in.
        records = catalog.records
        self._doc_tokens: list[list[str]] = [
            tokenize(self._document_for(record)) for record in records
        ]
        # Relevance is decided by term overlap, not by the sign of the BM25
        # score. BM25 IDF goes negative for a term present in more than half the
        # corpus, so scoring alone would discard every hit for a common word like
        # 日期 or 代码 — matched, ubiquitous, and silently dropped.
        self._doc_token_sets: list[set[str]] = [set(tokens) for tokens in self._doc_tokens]
        self._bm25 = BM25Okapi(self._doc_tokens) if self._doc_tokens else None

    def _market_rank(self, record: FieldRecord) -> int:
        """0 for in-scope or unknown markets, 1 for out-of-scope ones.

        Used as the primary sort key rather than as a score multiplier: BM25
        scores go negative for ubiquitous terms, and scaling a negative score
        *raises* it — a multiplicative penalty would promote exactly the fields
        it was meant to demote.

        An unknown market ranks with the preferred one. Absent metadata must not
        cost a field its place, or the 478 undescribed fields would sink out of
        every result and become unreachable.
        """
        if self.metadata is None or self.preferred_market is None:
            return 0
        meta = self.metadata.get(record.table, record.field)
        if meta is None or meta.market is None or meta.market == self.preferred_market:
            return 0
        return 1

    def _document_for(self, record: FieldRecord) -> str:
        parts = [record.table, record.field]
        meta = self.metadata.get(record.table, record.field) if self.metadata else None
        if meta is not None:
            parts.extend(part for part in (meta.name_zh, meta.description_zh) if part)
        return " ".join(parts)

    # ------------------------------------------------------------------ factories

    @classmethod
    def from_paths(
        cls,
        catalog_path: Path | str,
        aliases_path: Path | str,
        metadata_path: Path | str | None = None,
    ) -> FieldSearch:
        """Build a search from a generated JSONL catalog and an alias YAML.

        ``metadata_path`` is optional so the search still works before the
        dictionary has been synced; the constraint filters then behave as they
        did before the metadata tier existed.
        """
        catalog = FieldCatalog.load(catalog_path)
        aliases = _load_aliases(aliases_path)
        metadata = (
            MetadataCatalog.load(metadata_path)
            if metadata_path is not None and Path(metadata_path).exists()
            else None
        )
        return cls(catalog=catalog, aliases=aliases, metadata=metadata)

    @classmethod
    def from_aliases_path(cls, aliases_path: Path | str) -> FieldSearch:
        """Build the exact-alias tier when the licensed full catalog is absent.

        The checked-in alias registry is sufficient for known platform metrics;
        BM25 remains unavailable until an authorized local catalog is supplied.
        """
        return cls(catalog=FieldCatalog([]), aliases=_load_aliases(aliases_path))

    # ------------------------------------------------------------------ search

    def search(
        self,
        requirement: DataRequirement,
        limit: int = 10,
        *,
        use_adjusted_price: bool = True,
    ) -> list[FieldCandidate]:
        query = requirement.meaning.strip()
        req_asset = requirement.asset_type
        req_freq = requirement.frequency

        alias_hits = self._alias_candidates(query, req_asset, req_freq)
        # Pull a wider BM25 window so semantic rerank can still see adj/raw
        # close when they were not the alias hit. Truncation happens after
        # labelling, not by dropping unadjusted rows as a policy.
        pool_limit = max(limit * 4, limit)
        bm25_hits = self._bm25_candidates(query, req_asset, req_freq, limit=pool_limit)

        merged: list[FieldCandidate] = []
        seen: set[tuple[str, str]] = set()
        for cand in [*alias_hits, *bm25_hits]:
            key = (cand.table, cand.field)
            if key in seen:
                continue
            seen.add(key)
            merged.append(cand)
        return apply_price_semantics(
            merged,
            requirement,
            use_adjusted_price,
            inject=lambda field: self._semantic_candidate(field, req_asset, req_freq),
            limit=limit,
        )

    def _semantic_candidate(
        self,
        field: str,
        req_asset: AssetType | None,
        req_freq: Frequency | None,
    ) -> FieldCandidate | None:
        """Build a labelled extra row for a preferred/also-listed price field.

        ``source_tier`` is ``semantic`` so an injected adj-close is not pretended
        to be the ``收盘价`` alias. Existing hits keep whatever tier produced them.
        """
        wanted = field.lower()
        for entry in self.aliases.values():
            if entry.field != wanted:
                continue
            if not _passes_filter(entry.asset_type, entry.frequency, req_asset, req_freq):
                continue
            candidate = self._candidate_from_alias(entry, lexical_score=0.0)
            return candidate.model_copy(
                update={
                    "source_tier": "semantic",
                    "evidence": f"semantic:{entry.table}.{entry.field}",
                }
            )
        for record in self.catalog.records:
            if record.field != wanted:
                continue
            meta = (
                self.metadata.get(record.table, record.field) if self.metadata is not None else None
            )
            if not _passes_filter(
                meta.asset_type if meta else None,
                meta.frequency if meta else None,
                req_asset,
                req_freq,
            ):
                continue
            return FieldCandidate(
                table=record.table,
                field=record.field,
                meaning_zh=meta.name_zh if meta else "",
                asset_type=meta.asset_type if meta else None,
                frequency=meta.frequency if meta else None,
                unit=meta.unit if meta else None,
                metadata_source=meta.metadata_source if meta else None,
                source_tier="semantic",
                evidence=f"semantic:{record.table}.{record.field}",
            )
        if wanted in PRICE_ADJUSTMENT_BY_FIELD:
            return FieldCandidate(
                table="ashareeodprices",
                field=wanted,
                source_tier="semantic",
                evidence=f"semantic:ashareeodprices.{wanted}",
            )
        return None

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
                if _passes_filter(entry.asset_type, entry.frequency, req_asset, req_freq):
                    return [self._candidate_from_alias(entry, lexical_score=0.9)]
        return []

    def _candidate_from_alias(self, entry: AliasEntry, *, lexical_score: float) -> FieldCandidate:
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

        query_set = set(query_tokens)
        matched = [idx for idx, tokens in enumerate(self._doc_token_sets) if tokens & query_set]
        if not matched:
            return []

        assert self._bm25 is not None
        raw_scores = self._bm25.get_scores(query_tokens)
        scores = {idx: float(raw_scores[idx]) for idx in matched}
        # Pull more than `limit` so filtering can still fill the page.
        top_k = min(len(matched), max(limit * 4, limit))
        ranked_idx = sorted(
            matched,
            key=lambda i: (self._market_rank(self.catalog.records[i]), -scores[i]),
        )[:top_k]

        out: list[FieldCandidate] = []
        for idx in ranked_idx:
            score = scores[idx]
            record = self.catalog.records[idx]
            meta = (
                self.metadata.get(record.table, record.field) if self.metadata is not None else None
            )
            # A described field must match the constraints. An undescribed one
            # passes: `_passes_filter` treats None as "unknown, not excluded".
            if not _passes_filter(
                meta.asset_type if meta else None,
                meta.frequency if meta else None,
                req_asset,
                req_freq,
            ):
                continue
            out.append(
                FieldCandidate(
                    table=record.table,
                    field=record.field,
                    meaning_zh=meta.name_zh if meta else "",
                    asset_type=meta.asset_type if meta else None,
                    frequency=meta.frequency if meta else None,
                    unit=meta.unit if meta else None,
                    metadata_source=meta.metadata_source if meta else None,
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
