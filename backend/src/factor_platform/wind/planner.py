"""Deterministic retrieval planner: confirmed spec + confirmed fields -> ExecutionPlan.

By the time this runs, every judgement call has already been made by a human: the
user confirmed the canonical formula and confirmed which Wind column each variable
maps to. So the planner contains no model call and no heuristics about *what the
user meant*. It only decides *how to fetch it safely*, and it refuses when it
cannot.

Three things it does that the user did not ask for, because forgetting any of them
produces a plan that runs cleanly and returns wrong numbers:

* **Historical index membership before prices.** Pricing today's index members
  over a two-year window is survivorship bias, and the result looks fine.
* **A warm-up window.** A 20-day rolling return has no value on day 1; without
  extra history the first weeks are silently NaN or, worse, computed from a
  truncated window.
* **The time convention, written into the plan.** Retrieval and execution both
  need to know when the signal becomes knowable. Left implicit, a pipeline that
  reads no future field still trades on information it could not have had.

And two refusals, both of which are failures at planning time rather than
warnings: a variable with no confirmed field, and a point-in-time financial field
with no announcement date to gate it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any, Final

from factor_platform.domain.errors import DomainError
from factor_platform.domain.formula import ROLLING_OPERATORS, FormulaNode
from factor_platform.domain.models import (
    ExecutionPlan,
    ExecutionStep,
    FactorSpec,
    FieldSelection,
    FieldTimeRole,
    QueryShape,
    ResearchRequest,
)
from factor_platform.factor.metric_registry import MetricRegistry
from factor_platform.wind.adapter import ADJUSTED_PRICE_FIELD_MAP, PRICE_FIELD_MAP
from factor_platform.wind.capabilities import CapabilityCatalog
from factor_platform.wind.metadata_catalog import MetadataCatalog

_TOOL_PREFIX: Final = "wind."

#: The registered retrieval function, consulted for what it can actually serve.
_PRICE_TOOL: Final = "get_price"

# Calendar days per trading day, rounded up. Used only to size the warm-up
# window, which is then widened by the real calendar at execution time.
_CALENDAR_DAYS_PER_TRADING_DAY: Final = 1.6

_INDEX_SUFFIXES: Final = (".SH", ".SZ", ".CSI", ".CNI")


class PlanningError(DomainError):
    """Raised when a plan cannot be built without guessing."""


class WindPlanner:
    """Builds an :class:`ExecutionPlan` from confirmed inputs only."""

    def __init__(
        self,
        catalog: CapabilityCatalog,
        registry: MetricRegistry,
        metadata: MetadataCatalog | None = None,
    ) -> None:
        self._catalog = catalog
        self._registry = registry
        self._metadata = metadata
        # What the registered price function can actually produce. Asking the
        # catalog rather than hard-coding a table list keeps one source of truth:
        # get_price raises NotImplementedError on anything outside this set, and
        # a plan that discovers that at execution time has already wasted a run.
        price_tool = catalog.get_tool(_PRICE_TOOL)
        self._price_outputs: frozenset[str] = frozenset(
            output.output for output in (price_tool.exact_outputs if price_tool else ())
        )
        self._price_tables: frozenset[str] = frozenset(
            dependency.table.lower()
            for dependency in (price_tool.source_dependencies if price_tool else ())
        )
        self._price_output_by_source: dict[str, str] = {
            source.lower(): output
            for mapping in (PRICE_FIELD_MAP, ADJUSTED_PRICE_FIELD_MAP)
            for output, source in mapping.items()
        }

    def plan(
        self,
        spec: FactorSpec,
        confirmed: Sequence[FieldSelection],
        request: ResearchRequest,
    ) -> ExecutionPlan:
        bindings = {selection.logical_name: selection for selection in confirmed}
        self._reject_unconfirmed(spec, bindings)
        self._reject_disputed(bindings)

        warmup_start = self._warmup_start(spec.formula_ast, request.start_date)
        steps: list[ExecutionStep] = []

        if _is_index_universe(spec.universe):
            steps.append(self._membership_step(spec.universe, warmup_start, request))

        steps.extend(self._universe_filter_steps(spec, warmup_start, request))

        for variable in spec.variables:
            steps.append(
                self._retrieval_step(bindings[variable.logical_name], spec, warmup_start, request)
            )

        return ExecutionPlan(
            steps=steps,
            warmup_start=warmup_start,
            time_convention=spec.time_convention,
            metadata={
                "start_date": request.start_date,
                "end_date": request.end_date,
                "universe": spec.universe,
                "confirmed_fields": [f"{s.logical_name} -> {s.table}.{s.field}" for s in confirmed],
            },
        )

    # ------------------------------------------------------------------ refusals

    @staticmethod
    def _reject_unconfirmed(spec: FactorSpec, bindings: dict[str, FieldSelection]) -> None:
        missing = [v.logical_name for v in spec.variables if v.logical_name not in bindings]
        if missing:
            raise PlanningError(
                f"no confirmed Wind field for {', '.join(sorted(missing))}; "
                "planning around a guess is how the wrong column reaches production"
            )

    def _reject_disputed(self, bindings: dict[str, FieldSelection]) -> None:
        """Refuse a metric the registry has marked wrong.

        Checked by logical name, which is the term the user actually chose.
        Unregistered names are ordinary variables, not metric keys, so only a
        registered-and-disputed one stops the plan.
        """
        for name in bindings:
            if self._registry.get(name) is not None:
                self._registry.enforce(name)

    # ------------------------------------------------------------------ steps

    def _membership_step(
        self, universe: str, warmup_start: str, request: ResearchRequest
    ) -> ExecutionStep:
        """Resolve historical index members.

        ``find_exact`` returns membership tools with empty arguments — the
        capability registry describes them semantically — so the arguments are
        filled from the trusted request envelope here.
        """
        return ExecutionStep(
            tool=f"{_TOOL_PREFIX}index_components",
            purpose="resolve historical index membership before any price is fetched",
            arguments={
                "order_book_id": universe,
                "start_date": warmup_start,
                "end_date": request.end_date,
            },
            validation=["members must be resolved per rebalance date, not as of today"],
        )

    def _universe_filter_steps(
        self, spec: FactorSpec, warmup_start: str, request: ResearchRequest
    ) -> list[ExecutionStep]:
        steps: list[ExecutionStep] = []
        rules = spec.data_rules
        if rules.exclude_st:
            steps.append(
                ExecutionStep(
                    tool=f"{_TOOL_PREFIX}is_st_stock",
                    purpose="exclude ST names per the confirmed data rules",
                    arguments={
                        "order_book_ids": "$universe",
                        "start_date": warmup_start,
                        "end_date": request.end_date,
                    },
                )
            )
        if rules.exclude_suspended:
            steps.append(
                ExecutionStep(
                    tool=f"{_TOOL_PREFIX}is_suspended",
                    purpose="exclude suspended names per the confirmed data rules",
                    arguments={
                        "order_book_ids": "$universe",
                        "start_date": warmup_start,
                        "end_date": request.end_date,
                    },
                )
            )
        return steps

    def _retrieval_step(
        self,
        selection: FieldSelection,
        spec: FactorSpec,
        warmup_start: str,
        request: ResearchRequest,
    ) -> ExecutionStep:
        price_output = self._price_output_by_source.get(selection.field.lower())
        served_by_price_tool = (
            price_output in self._price_outputs
            and selection.table.lower() in self._price_tables
            and not selection.point_in_time
        )
        if served_by_price_tool:
            return ExecutionStep(
                tool=f"{_TOOL_PREFIX}get_price",
                purpose=f"fetch {selection.logical_name} via the registered price function",
                arguments={
                    "order_book_ids": "$universe",
                    "start_date": warmup_start,
                    "end_date": request.end_date,
                    "fields": [price_output],
                    "adjust_type": "post" if spec.data_rules.use_adjusted_price else "none",
                },
                inputs=[selection.logical_name],
            )
        return self._generic_step(selection, spec, warmup_start, request)

    def _generic_step(
        self,
        selection: FieldSelection,
        spec: FactorSpec,
        warmup_start: str,
        request: ResearchRequest,
    ) -> ExecutionStep:
        shape = _shape_for(selection)
        arguments: dict[str, Any] = {
            "query_shape": shape.value,
            "table_name": selection.table,
            "selected_fields": [selection.field],
            "code_field": "s_info_windcode",
            "order_book_ids": "$universe",
            "start_date": warmup_start,
            "end_date": request.end_date,
        }

        if shape in {QueryShape.POINT_RANGE, QueryShape.CROSS_SECTION_ASOF}:
            arguments["observation_date"] = "trade_dt"
        elif shape is QueryShape.ANNOUNCEMENT_RANGE:
            arguments["announcement_date"] = selection.announcement_date_field or selection.field

        if selection.point_in_time or shape is QueryShape.REPORT_PERIOD:
            if not selection.announcement_date_field:
                raise PlanningError(
                    f"{selection.logical_name} is point-in-time but its confirmed "
                    "binding carries no announcement date field; without one there "
                    "is no way to know when the value became public"
                )
            arguments["announcement_date"] = selection.announcement_date_field
            arguments["report_period"] = selection.report_period_field or "report_period"
            arguments["as_of_offset_days"] = _announcement_offset(spec)
            # A report announced near the beginning of the requested window has
            # a report_period in the previous year. Fetch enough prior periods
            # for point-in-time alignment instead of starting at the signal date.
            arguments["start_date"] = (
                date.fromisoformat(request.start_date) - timedelta(days=730)
            ).isoformat()
            arguments["as_of_date"] = request.end_date

        return ExecutionStep(
            tool=f"{_TOOL_PREFIX}execute_generic_query_plan",
            purpose=(
                f"fetch {selection.logical_name} via the {shape.value} query shape "
                "(no registered function covers this column)"
            ),
            arguments=arguments,
            inputs=[selection.logical_name],
            validation=(
                ["no row may be visible before its announcement date"]
                if selection.point_in_time
                else []
            ),
        )

    # ------------------------------------------------------------------ warm-up

    @staticmethod
    def _warmup_start(node: FormulaNode, start_date: str) -> str:
        """Reach back far enough that the first requested day has a full window."""
        window = _largest_window(node)
        if window <= 0:
            return start_date
        calendar_days = int(window * _CALENDAR_DAYS_PER_TRADING_DAY) + 5
        return (date.fromisoformat(start_date) - timedelta(days=calendar_days)).isoformat()


def _largest_window(node: FormulaNode) -> int:
    """Largest rolling window anywhere in the AST."""
    window = 0
    if node.op in ROLLING_OPERATORS and node.params:
        raw = node.params.get("window")
        if isinstance(raw, int | float):
            window = int(raw)
    for child in node.args or []:
        window = max(window, _largest_window(child))
    return window


def _is_index_universe(universe: str) -> bool:
    return any(universe.upper().endswith(suffix) for suffix in _INDEX_SUFFIXES)


def _shape_for(selection: FieldSelection) -> QueryShape:
    if selection.time_role is FieldTimeRole.REPORT_PERIOD or selection.point_in_time:
        return QueryShape.REPORT_PERIOD
    if selection.time_role is FieldTimeRole.ANNOUNCEMENT:
        return QueryShape.ANNOUNCEMENT_RANGE
    if selection.time_role is None:
        return QueryShape.STATIC_LOOKUP
    return QueryShape.POINT_RANGE


def _announcement_offset(spec: FactorSpec) -> int:
    """Days to defer a financial value past its announcement date.

    Conservative is the default and the only safe choice when the publication
    moment is unknown: a filing released after the close is not tradeable that
    day, and assuming otherwise manufactures look-ahead that no downstream check
    would catch.
    """
    return 1 if spec.time_convention.announcement_timing_policy == "conservative" else 0


__all__ = ["PlanningError", "WindPlanner"]
