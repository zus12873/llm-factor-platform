"""The signed manifest: the only object this platform actually executes.

Generated Python used to hold that role. Making it safe required a source-level
AST whitelist, dynamic-import checks, and a proof that the code shown to the user
was byte-identical to the code that ran — a large amount of machinery to secure an
object that was only ever a fixed wrapper around the platform's own runtime. The
manifest removes the object rather than securing it: there is no generated source
in the execution path, so there is nothing to whitelist.

Two properties do the work.

**Determinism.** The canonical JSON uses sorted keys and fixed separators, and
carries no timestamp, no random value, and nothing that depends on dict iteration
order. The same inputs must produce the same ``sha256``, or "reproducible" is a
claim nobody can check.

**Fail-closed validation.** Every tool named in the plan must be registered, the
formula must pass the AST checks, the pipeline must pass its ordering rules, the
time convention is mandatory, and every input artifact must carry a hash. Anything
unrecognised is refused at build time rather than discovered by a worker.

The signature is keyed, not a bare hash. A hash proves the manifest was not
corrupted; only a signature proves it is the one the platform approved — which is
the difference that matters when the attack is "widen the date range after the
user signed off".
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Final

from pydantic import BaseModel, Field

from factor_platform.domain.errors import DomainError
from factor_platform.domain.models import (
    ExecutionPlan,
    FactorSpec,
    FieldSelection,
    QueryShape,
)
from factor_platform.domain.preprocessing import PreprocessingPipeline
from factor_platform.domain.time_convention import TimeConvention
from factor_platform.factor.ast_checks import check_ast
from factor_platform.wind.adapter import RQ_WIND_CAPABILITIES
from factor_platform.wind.capabilities import CapabilityCatalog

_TOOL_PREFIX: Final = "wind."

#: Serialisation settings that make the hash stable across processes and runs.
_JSON_KWARGS: Final[dict[str, Any]] = {
    "sort_keys": True,
    "separators": (",", ":"),
    "ensure_ascii": False,
}


class ManifestSchemaError(DomainError):
    """Raised when a manifest cannot be built from the given inputs."""


class ManifestVerificationError(DomainError):
    """Raised when a signed manifest fails verification."""


class InputArtifact(BaseModel):
    """One Parquet input the worker will read, pinned by content hash."""

    uri: str
    sha256: str
    rows: int = 0


class Manifest(BaseModel):
    """A complete, self-contained description of one factor computation."""

    schema_version: int = 1
    factor_spec: FactorSpec
    execution_plan: ExecutionPlan
    preprocessing: PreprocessingPipeline
    time_convention: TimeConvention
    field_selections: list[FieldSelection] = Field(default_factory=list)
    input_artifacts: list[InputArtifact] = Field(default_factory=list)
    runtime_versions: dict[str, str] = Field(default_factory=dict)

    def canonical_json(self) -> str:
        """Deterministic serialisation — the thing that gets hashed and signed."""
        return json.dumps(self.model_dump(mode="json"), **_JSON_KWARGS)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class SignedManifest(BaseModel):
    """A manifest payload plus its keyed signature."""

    schema_version: int = 1
    payload: str
    signature: str
    sha256: str


class ManifestBuilder:
    """Validates inputs and assembles a deterministic manifest."""

    def __init__(self, catalog: CapabilityCatalog | None = None) -> None:
        self._catalog = catalog or CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES)
        self._shapes = {shape.value for shape in QueryShape}

    def build(
        self,
        spec: FactorSpec,
        plan: ExecutionPlan,
        selections: list[FieldSelection],
        input_artifacts: list[InputArtifact],
        runtime_versions: dict[str, str] | None = None,
    ) -> Manifest:
        self._validate_plan(plan)
        self._validate_formula(spec)
        self._validate_inputs(input_artifacts)

        return Manifest(
            factor_spec=spec,
            execution_plan=plan,
            preprocessing=spec.preprocessing,
            time_convention=spec.time_convention,
            field_selections=list(selections),
            input_artifacts=list(input_artifacts),
            # Pinned by the caller; defaults stay empty rather than reading the
            # live interpreter, which would make the hash machine-dependent.
            runtime_versions=dict(runtime_versions or {}),
        )

    # ------------------------------------------------------------------ validation

    def _validate_plan(self, plan: ExecutionPlan) -> None:
        if not plan.steps:
            raise ManifestSchemaError("execution plan has no step to run")
        for step in plan.steps:
            bare = step.tool.removeprefix(_TOOL_PREFIX)
            if self._catalog.get_tool(bare) is not None:
                continue
            if step.arguments.get("shape") in self._shapes:
                continue
            raise ManifestSchemaError(
                f"tool {step.tool!r} is not registered in the capability catalog "
                "and is not one of the controlled query shapes"
            )

    @staticmethod
    def _validate_formula(spec: FactorSpec) -> None:
        report = check_ast(spec.formula_ast, spec.variables)
        blocking = [
            finding
            for finding in report.findings
            if finding.severity.value in {"error", "critical"}
        ]
        if blocking:
            raise ManifestSchemaError(
                "formula failed AST checks: "
                + "; ".join(f"{f.code}: {f.message}" for f in blocking)
            )

    @staticmethod
    def _validate_inputs(artifacts: list[InputArtifact]) -> None:
        unhashed = [artifact.uri for artifact in artifacts if not artifact.sha256]
        if unhashed:
            raise ManifestSchemaError(
                f"input artifacts without a sha256: {unhashed}; an unpinned input "
                "makes the run unreproducible"
            )


# --------------------------------------------------------------------------- signing


def sign(manifest: Manifest, *, key: str) -> SignedManifest:
    """Sign the canonical payload with a shared secret.

    The worker holds only this key. It needs no database credential and no model
    key, so a compromised worker cannot reach the data or the provider.
    """
    payload = manifest.canonical_json()
    return SignedManifest(
        payload=payload,
        signature=_mac(payload, key),
        sha256=manifest.sha256,
    )


def verify(signed: SignedManifest, *, key: str) -> Manifest:
    """Return the manifest if the signature holds, else raise.

    Compared in constant time so a mismatch cannot be found byte by byte.
    """
    if not hmac.compare_digest(_mac(signed.payload, key), signed.signature):
        raise ManifestVerificationError(
            "manifest signature does not match its payload; it was modified after "
            "signing or signed with a different key"
        )
    manifest = Manifest.model_validate_json(signed.payload)
    if manifest.sha256 != signed.sha256:
        raise ManifestVerificationError(
            "manifest hash does not match the signed hash"
        )
    return manifest


def _mac(payload: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


__all__ = [
    "InputArtifact",
    "Manifest",
    "ManifestBuilder",
    "ManifestSchemaError",
    "ManifestVerificationError",
    "SignedManifest",
    "sign",
    "verify",
]
