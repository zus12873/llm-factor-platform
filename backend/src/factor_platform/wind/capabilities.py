"""Normalize the Wind capability registry into planner tool contracts.

``RQ_WIND_CAPABILITIES`` (defined in :mod:`factor_platform.wind.adapter`) is
the source-of-truth registry of what the Wind replica can do. This module
projects that registry into clean, immutable tool specs that the deterministic
planner (Task 11) and LLM tool-calling can consume without knowing the
adapter's call surface.

Responsibilities:

* Normalize the registry's nested dicts into frozen dataclasses.
* Expose only callable data tools via :meth:`CapabilityCatalog.to_llm_tools` —
  ``lifecycle`` (``init``) and ``expression`` (``Factor``, ``LOG``) entries
  are excluded because they are not standalone data lookups; they are
  orchestration / expression-construction primitives consumed by other tools.
* Resolve a Chinese intent string to a concrete ``(tool_name, arguments)``
  match via :meth:`CapabilityCatalog.find_exact`.

This module is pure data normalization: no I/O, no DB calls, no credentials,
no Settings read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from factor_platform.wind.adapter import (
    PRICE_FIELD_MAP,
    RQ_WIND_CAPABILITIES,
)

#------------------------------------------------------------------------------
# Chinese intent → canonical field / adjustment lexicons.
#
# Deliberately tight and hand-curated for the price-term family the registry's
# ``exact_outputs`` already knows how to fulfill. Every value in
# ``_PRICE_INTENT_LEXICON`` MUST be a key of ``PRICE_FIELD_MAP`` (the adapter's
# Wind column mapping) so the lexicon cannot drift away from what the adapter
# actually serves. The assertion below enforces that invariant at import time.
# Non-price capabilities (calendar/status/membership/factor) do not need
# field-level argument resolution; ``find_exact`` falls back to their
# ``semantic_outputs`` intents.
#------------------------------------------------------------------------------
_PRICE_INTENT_LEXICON: dict[str, str] = {
    "收盘价": "close",
    "开盘价": "open",
    "最高价": "high",
    "最低价": "low",
    "成交量": "volume",
    "成交额": "total_turnover",
    "昨收": "prev_close",
    "前收盘": "prev_close",
    "涨停价": "limit_up",
    "跌停价": "limit_down",
}

_ADJUST_QUALIFIERS: dict[str, str] = {
    "后复权": "post",
    "前复权": "pre",
    "不复权": "none",
}

assert set(_PRICE_INTENT_LEXICON.values()).issubset(PRICE_FIELD_MAP.keys()), (
    "Price intent lexicon must only reference canonical PRICE_FIELD_MAP fields."
)

# Kinds that are NOT direct data tools: ``lifecycle`` is orchestration,
# ``expression`` primitives (Factor, LOG) only make sense as arguments to
# ``execute_factor``.
_NON_TOOL_KINDS: frozenset[str] = frozenset({"lifecycle", "expression"})


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """A single tool parameter: required flag, default, and human meaning."""

    required: bool
    default: Any = None
    meaning: str = ""


@dataclass(frozen=True, slots=True)
class SourceDependency:
    """A Wind table the tool reads from, with the columns it consumes."""

    table: str
    fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExactOutput:
    """A directly-addressable output: given ``argument`` X, the tool yields ``field``.

    For ``get_price`` the ``argument`` looks like ``{"fields": ["close"],
    "adjust_type": "none"}`` and ``output`` is the canonical column ``close``.
    Non-price tools generally have no exact_outputs.
    """

    field: str
    output: str
    coverage: str = "exact"
    argument: Mapping[str, Any] = dc_field(default_factory=dict)
    tables: tuple[str, ...] = ()
    table: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticOutput:
    """A higher-level named output with Chinese intent labels."""

    name: str
    type: str
    intents: tuple[str, ...] = ()
    meaning: str = ""
    source_formula: str = ""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Normalized, immutable projection of one registry entry."""

    name: str
    kind: str
    purpose: str
    asset_types: tuple[str, ...]
    parameters: Mapping[str, ParameterSpec]
    source_dependencies: tuple[SourceDependency, ...]
    exact_outputs: tuple[ExactOutput, ...]
    semantic_outputs: tuple[SemanticOutput, ...]
    return_schema: Mapping[str, Any]
    constraints: tuple[str, ...]
    planner: str
    examples: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IntentMatch:
    """A successful ``find_exact`` result: which tool, which arguments."""

    tool_name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CapabilityCatalog:
    """Immutable catalog of normalized Wind tool specs.

    Build via ``CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES)``. The
    catalog stores tools in registry order and exposes deterministic lookups;
    iteration order of :meth:`to_llm_tools` therefore matches the registry.
    """

    tools: tuple[ToolSpec, ...]

    #------------------------------------------------------------------
    # Construction
    #------------------------------------------------------------------
    @classmethod
    def from_registry(
        cls, registry: Mapping[str, Mapping[str, Any]]
    ) -> CapabilityCatalog:
        """Build a catalog from a raw registry mapping.

        Defensive: missing / extra keys are silently skipped rather than
        raising, so the registry can carry forward-compatible fields (e.g.
        ``execute_factor.factor_expressions``) without breaking normalization.
        """
        tools = tuple(_normalize(name, entry) for name, entry in registry.items())
        return cls(tools=tools)

    #------------------------------------------------------------------
    # Public lookups
    #------------------------------------------------------------------
    def to_llm_tools(self) -> tuple[ToolSpec, ...]:
        """Data tools exposed to the planner / LLM.

        Excludes ``lifecycle`` (``init``) and ``expression`` (``Factor``,
        ``LOG``): those are not standalone data lookups. Preserves registry
        ordering.
        """
        return tuple(tool for tool in self.tools if tool.kind not in _NON_TOOL_KINDS)

    def get_tool(self, name: str) -> ToolSpec | None:
        """Return the tool spec by registry name, or ``None`` if absent."""
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    def find_exact(self, intent: str) -> IntentMatch | None:
        """Resolve a Chinese intent to a concrete ``(tool, arguments)`` match.

        Strategy (data-driven, no LLM):

        1. Detect an optional 复权 qualifier (``后复权``→``post``, ``前复权``→
           ``pre``, ``不复权``→``none``) and a price-field token via the
           curated lexicon (``收盘价``→``close``, ``开盘价``→``open``, ...).
        2. Search the LLM tools' ``exact_outputs`` for entries whose
           ``argument.fields`` equals ``[matched_field]``; prefer one whose
           ``argument.adjust_type`` matches the detected qualifier.
        3. Fall back to ``semantic_outputs`` intent matching (covers
           calendar / status / membership tools that have no exact_outputs).

        Returns ``None`` if nothing applies. The returned ``arguments`` is a
        fresh dict safe for callers to mutate without corrupting the catalog.
        """
        adjust_type = _detect_adjustment(intent)
        field_token = _detect_price_field(intent)

        if field_token is not None:
            match = self._match_exact_output(field_token, adjust_type)
            if match is not None:
                return match

        return self._match_semantic(intent)

    #------------------------------------------------------------------
    # Internal helpers
    #------------------------------------------------------------------
    def _match_exact_output(
        self, field_token: str, adjust_type: str | None
    ) -> IntentMatch | None:
        matches: list[tuple[str, Mapping[str, Any]]] = []
        for tool in self.to_llm_tools():
            for out in tool.exact_outputs:
                fields = out.argument.get("fields")
                if isinstance(fields, list) and fields == [field_token]:
                    matches.append((tool.name, out.argument))
        if not matches:
            return None
        if adjust_type is not None:
            for name, args in matches:
                if args.get("adjust_type") == adjust_type:
                    return IntentMatch(tool_name=name, arguments=dict(args))
        name, args = matches[0]
        return IntentMatch(tool_name=name, arguments=dict(args))

    def _match_semantic(self, intent: str) -> IntentMatch | None:
        for tool in self.to_llm_tools():
            for sem in tool.semantic_outputs:
                if any(label and label in intent for label in sem.intents):
                    return IntentMatch(tool_name=tool.name, arguments={})
        return None


#------------------------------------------------------------------------------
# Normalization helpers — turn the registry's raw nested dicts into frozen
# dataclasses. Defensive: every accessor uses ``.get(key, default)`` so the
# registry can omit fields without breaking normalization.
#------------------------------------------------------------------------------
def _normalize(name: str, entry: Mapping[str, Any]) -> ToolSpec:
    return ToolSpec(
        name=name,
        kind=str(entry.get("kind", "")),
        purpose=str(entry.get("purpose", "")),
        asset_types=tuple(str(a) for a in entry.get("asset_types", [])),
        parameters={
            key: _normalize_parameter(value)
            for key, value in entry.get("parameters", {}).items()
        },
        source_dependencies=tuple(
            _normalize_source(dep) for dep in entry.get("source_dependencies", [])
        ),
        exact_outputs=tuple(
            _normalize_exact(out) for out in entry.get("exact_outputs", [])
        ),
        semantic_outputs=tuple(
            _normalize_semantic(out) for out in entry.get("semantic_outputs", [])
        ),
        return_schema=dict(entry.get("return_schema", {})),
        constraints=tuple(str(c) for c in entry.get("constraints", [])),
        planner=str(entry.get("planner", "")),
        examples=tuple(str(e) for e in entry.get("examples", [])),
    )


def _normalize_parameter(raw: Mapping[str, Any]) -> ParameterSpec:
    return ParameterSpec(
        required=bool(raw.get("required", False)),
        default=raw.get("default"),
        meaning=str(raw.get("meaning", "")),
    )


def _normalize_source(raw: Mapping[str, Any]) -> SourceDependency:
    return SourceDependency(
        table=str(raw.get("table", "")),
        fields=tuple(str(f) for f in raw.get("fields", [])),
    )


def _normalize_exact(raw: Mapping[str, Any]) -> ExactOutput:
    tables_value = raw.get("tables")
    tables = (
        tuple(str(t) for t in tables_value) if isinstance(tables_value, list) else ()
    )
    argument_raw = raw.get("argument")
    return ExactOutput(
        field=str(raw.get("field", "")),
        output=str(raw.get("output", "")),
        coverage=str(raw.get("coverage", "exact")),
        argument=dict(argument_raw) if isinstance(argument_raw, Mapping) else {},
        tables=tables,
        table=raw.get("table"),
    )


def _normalize_semantic(raw: Mapping[str, Any]) -> SemanticOutput:
    return SemanticOutput(
        name=str(raw.get("name", "")),
        type=str(raw.get("type", "")),
        intents=tuple(str(i) for i in raw.get("intents", [])),
        meaning=str(raw.get("meaning", "")),
        source_formula=str(raw.get("source_formula", "")),
    )


def _detect_price_field(intent: str) -> str | None:
    """Return the canonical field token for a Chinese price term, or ``None``.

    Aligned with ``PRICE_FIELD_MAP`` by the import-time assertion above: a
    lexicon value that is not a real adapter field would be a bug, not a
    silent miss.
    """
    for label, field_token in _PRICE_INTENT_LEXICON.items():
        if label in intent:
            return field_token
    return None


def _detect_adjustment(intent: str) -> str | None:
    """Return the ``adjust_type`` qualifier expressed in the intent, or ``None``."""
    for label, adjust_type in _ADJUST_QUALIFIERS.items():
        if label in intent:
            return adjust_type
    return None


__all__ = [
    "CapabilityCatalog",
    "ExactOutput",
    "IntentMatch",
    "ParameterSpec",
    "SemanticOutput",
    "SourceDependency",
    "ToolSpec",
    "RQ_WIND_CAPABILITIES",
]
