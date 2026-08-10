"""Pydantic domain contracts for the factor platform.

These types are the single source of truth shared by every backend module. Domain
contracts contain no I/O: they describe what flows between stages, not how it is
fetched or executed. Later modules import these types rather than defining local
dicts.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from factor_platform.domain.formula import FormulaNode
from factor_platform.domain.preprocessing import DataRules, PreprocessingPipeline
from factor_platform.domain.time_convention import TimeConvention

# --------------------------------------------------------------------------- enums


class AssetType(StrEnum):
    ALL = "all"
    STOCK = "stock"
    INDEX = "index"
    FUND = "fund"
    BOND = "bond"
    FUTURES = "futures"
    OPTIONS = "options"


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class FactorDirection(StrEnum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"
    NEUTRAL = "neutral"


class FieldTimeRole(StrEnum):
    """How a field's timestamp is interpreted for point-in-time correctness."""

    OBSERVATION = "observation"
    ANNOUNCEMENT = "announcement"
    REPORT_PERIOD = "report_period"
    AS_OF = "as_of"


class QueryShape(StrEnum):
    """The six generic Wind query shapes supported by the adapter."""

    POINT_RANGE = "point_range"
    REPORT_PERIOD = "report_period"
    ANNOUNCEMENT_RANGE = "announcement_range"
    INTERVAL_OVERLAP = "interval_overlap"
    STATIC_LOOKUP = "static_lookup"
    CROSS_SECTION_ASOF = "cross_section_asof"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ErrorCategory(StrEnum):
    INPUT = "input"
    FIELD = "field"
    EMPTY_DATA = "empty_data"
    TIME_BASIS = "time_basis"
    FORMULA = "formula"
    RESOURCE = "resource"
    INFRASTRUCTURE = "infrastructure"


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


# --------------------------------------------------------------------------- evidence & variables


class ReportEvidence(BaseModel):
    """A piece of source text that justifies an extracted value."""

    schema_version: int = 1
    page_number: int | None = None
    quote: str = ""
    language: str | None = None
    formula_context: str | None = None
    confidence: float | None = None
    source: str = "text"


class DataRequirement(BaseModel):
    """A single logical variable needed by a factor, with its data semantics."""

    schema_version: int = 1
    logical_name: str
    meaning: str = ""
    asset_type: AssetType | None = None
    frequency: Frequency | None = None
    unit: str | None = None
    point_in_time_required: bool = False
    financial_period: str | None = None
    announcement_date_required: bool = False


class Ambiguity(BaseModel):
    """An unresolved choice persisted on a FactorSpec (e.g. missing rebalance)."""

    schema_version: int = 1
    field: str
    reason: str = ""
    options: list[str] = Field(default_factory=list)
    recommended: str | None = None
    blocking: bool = False


class ClarificationQuestion(BaseModel):
    """A question presented to the user, possibly blocking further progress."""

    schema_version: int = 1
    question_id: str
    question: str
    field: str | None = None
    options: list[str] = Field(default_factory=list)
    recommended: str | None = None
    reason: str = ""
    blocking: bool = False
    target_version: int = 1


# --------------------------------------------------------------------------- spec & request


class FactorSpec(BaseModel):
    """The confirmed, machine-checkable description of one factor.

    ``formula_ast`` is the single source of truth. ``canonical_formula`` is
    rendered from it by the backend and is what the user actually confirms;
    ``formula_explanation`` is the model's prose and is display-only. The model
    never authors the confirmed formula, so what is signed off and what is
    executed cannot diverge.
    """

    schema_version: int = 1
    version: int = 1
    factor_name: str
    hypothesis: str = ""
    asset_type: AssetType
    universe: str
    frequency: Frequency
    rebalance_frequency: Frequency | None = None
    direction: FactorDirection | None = None
    formula_ast: FormulaNode
    canonical_formula: str = ""
    formula_explanation: str = ""
    variables: list[DataRequirement] = Field(default_factory=list)
    data_rules: DataRules = Field(default_factory=DataRules)
    preprocessing: PreprocessingPipeline = Field(default_factory=PreprocessingPipeline)
    time_convention: TimeConvention = Field(default_factory=TimeConvention)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    source_evidence: list[ReportEvidence] = Field(default_factory=list)


class ResearchRequest(BaseModel):
    """A user's natural-language research idea plus its evaluation envelope."""

    schema_version: int = 1
    asset_type: AssetType
    universe: str
    start_date: str
    end_date: str
    research_idea: str
    language: str = "zh"
    frequency: Frequency = Frequency.DAILY
    direction: FactorDirection | None = None
    report_artifact_id: str | None = None
    data_rules: DataRules = Field(default_factory=DataRules)
    preprocessing: PreprocessingPipeline = Field(default_factory=PreprocessingPipeline)
    time_convention: TimeConvention = Field(default_factory=TimeConvention)


# --------------------------------------------------------------------------- fields


class FieldCandidate(BaseModel):
    """A Wind field proposed to satisfy a DataRequirement."""

    schema_version: int = 1
    table: str
    field: str
    meaning_zh: str = ""
    meaning_en: str = ""
    asset_type: AssetType | None = None
    frequency: Frequency | None = None
    time_role: FieldTimeRole | None = None
    unit: str | None = None
    # ``None`` means the local dictionary could not describe this field. It is
    # still a legitimate candidate; the user simply gets no description with it.
    metadata_source: str | None = None
    source_tier: str = "bm25"
    lexical_score: float | None = None
    recommendation_score: float | None = None
    evidence: str | None = None


class FieldSelection(BaseModel):
    """A user-confirmed mapping from a logical variable to a concrete Wind field."""

    schema_version: int = 1
    logical_name: str
    table: str
    field: str
    time_role: FieldTimeRole | None = None
    point_in_time: bool = False
    announcement_date_field: str | None = None
    report_period_field: str | None = None


# --------------------------------------------------------------------------- execution plan


class ExecutionStep(BaseModel):
    """One deterministic retrieval/computation step (tool + bounded arguments)."""

    schema_version: int = 1
    tool: str
    purpose: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    postprocessing: dict[str, Any] | None = None
    validation: list[str] = Field(default_factory=list)
    failure_strategy: str = "fail"


class ExecutionPlan(BaseModel):
    """An ordered, fully-determined retrieval plan.

    Carries its own ``time_convention`` rather than reading it back from the spec:
    the plan is what gets signed, queued and replayed, so the timing rules that
    decide when a signal is knowable have to travel with it.
    """

    schema_version: int = 1
    steps: list[ExecutionStep] = Field(default_factory=list)
    warmup_start: str | None = None
    time_convention: TimeConvention = Field(default_factory=TimeConvention)
    metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- validation


class ValidationFinding(BaseModel):
    schema_version: int = 1
    severity: ValidationSeverity
    code: str
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    schema_version: int = 1
    findings: list[ValidationFinding] = Field(default_factory=list)

    def has_error(self, code: str) -> bool:
        return self.has(ValidationSeverity.ERROR, code)

    def has_warning(self, code: str) -> bool:
        return self.has(ValidationSeverity.WARNING, code)

    def has(self, severity: ValidationSeverity, code: str) -> bool:
        return any(
            finding.severity == severity and finding.code == code for finding in self.findings
        )


# --------------------------------------------------------------------------- execution result


class StructuredError(BaseModel):
    schema_version: int = 1
    category: ErrorCategory
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    repairable: bool = False


class ExecutionResult(BaseModel):
    schema_version: int = 1
    status: ExecutionStatus
    artifact_uri: str | None = None
    data_validation: ValidationReport | None = None
    formula_validation: ValidationReport | None = None
    result_validation: ValidationReport | None = None
    log_summary: str = ""
    resource_stats: dict[str, Any] = Field(default_factory=dict)
    errors: list[StructuredError] = Field(default_factory=list)
    revision_suggestion: str | None = None


# --------------------------------------------------------------------------- library & session


class FactorArtifact(BaseModel):
    """An immutable, versioned, published factor (library entry)."""

    schema_version: int = 1
    factor_id: str
    version: int
    source_session_id: str
    factor_name: str
    spec: FactorSpec
    plan: ExecutionPlan | None = None
    program_sha256: str
    result_sha256: str | None = None
    artifact_uri: str | None = None
    creator: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SessionSnapshot(BaseModel):
    """The folded, current state of a session, fully derivable from its events."""

    schema_version: int = 1
    session_id: str
    state: str
    version: int
    request: ResearchRequest | None = None
    factor_spec: FactorSpec | None = None
    field_selections: list[FieldSelection] = Field(default_factory=list)
    plan: ExecutionPlan | None = None
    generated_code: str | None = None
    code_sha256: str | None = None
    execution_result: ExecutionResult | None = None
    artifact_uri: str | None = None
    last_error: StructuredError | None = None
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    clarifications: list[ClarificationQuestion] = Field(default_factory=list)


__all__ = [
    "Ambiguity",
    "AssetType",
    "ClarificationQuestion",
    "DataRequirement",
    "DataRules",
    "ErrorCategory",
    "ExecutionPlan",
    "ExecutionResult",
    "ExecutionStatus",
    "ExecutionStep",
    "FactorArtifact",
    "FactorDirection",
    "FactorSpec",
    "FieldCandidate",
    "FieldSelection",
    "FieldTimeRole",
    "Frequency",
    "PreprocessingPipeline",
    "QueryShape",
    "ReportEvidence",
    "ResearchRequest",
    "SessionSnapshot",
    "StructuredError",
    "TimeConvention",
    "ValidationFinding",
    "ValidationReport",
    "ValidationSeverity",
]
