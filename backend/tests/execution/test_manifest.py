"""Tests for the signed execution manifest.

The manifest is the only thing the platform actually executes. Generated Python
used to hold that role, which forced a source-level AST whitelist, dynamic-import
checks, and a proof that the code shown to the user was byte-identical to the code
that ran — a lot of machinery to make a hazardous object safe, when the object was
only ever a fixed wrapper around the platform's own runtime.

So the properties that matter here are: the same inputs produce the same manifest
(otherwise "reproducible" is a claim nobody can check), every tool named in it is
registered (fail-closed against a plan that grew an unexpected step), and any edit
after signing is detected.
"""

from __future__ import annotations

import pytest

from factor_platform.domain.models import (
    ExecutionPlan,
    ExecutionStep,
    FactorSpec,
    FieldSelection,
    FieldTimeRole,
)
from factor_platform.execution.manifest import (
    InputArtifact,
    ManifestBuilder,
    ManifestSchemaError,
    ManifestVerificationError,
    sign,
    verify,
)

SIGNING_KEY = "unit-test-only-signing-key"  # pragma: allowlist secret


def spec() -> FactorSpec:
    return FactorSpec.model_validate(
        {
            "factor_name": "momentum",
            "asset_type": "stock",
            "universe": "000300.SH",
            "frequency": "daily",
            "direction": "higher_is_better",
            "canonical_formula": "rank(rolling_return(close, window=20))",
            "formula_ast": {
                "type": "call",
                "op": "rank",
                "args": [
                    {
                        "type": "call",
                        "op": "rolling_return",
                        "args": [{"type": "variable", "name": "close"}],
                        "params": {"window": 20},
                    }
                ],
            },
            "variables": [{"logical_name": "close", "meaning": "后复权收盘价"}],
        }
    )


def plan(tool: str = "wind.get_price") -> ExecutionPlan:
    return ExecutionPlan(
        steps=[
            ExecutionStep(
                tool=tool,
                arguments={
                    "order_book_ids": "$universe",
                    "start_date": "2023-11-01",
                    "end_date": "2024-06-30",
                    "fields": ["close"],
                },
            )
        ],
        warmup_start="2023-11-01",
        metadata={"start_date": "2024-01-01", "end_date": "2024-06-30"},
    )


def selections() -> list[FieldSelection]:
    return [
        FieldSelection(
            logical_name="close",
            table="ashareeodprices",
            field="s_dq_adjclose",
            time_role=FieldTimeRole.OBSERVATION,
        )
    ]


def inputs() -> list[InputArtifact]:
    return [InputArtifact(uri="file:///artifacts/close.parquet", sha256="a" * 64, rows=1000)]


def build(**overrides: object) -> object:
    builder = ManifestBuilder()
    return builder.build(
        overrides.get("spec", spec()),
        overrides.get("plan", plan()),
        overrides.get("selections", selections()),
        overrides.get("inputs", inputs()),
    )


# --------------------------------------------------------------------------- determinism


def test_same_inputs_build_an_identical_manifest() -> None:
    """Without this, "reproducible" is a claim no one can check."""
    assert build().sha256 == build().sha256


def test_a_different_window_changes_the_hash() -> None:
    other = spec()
    other.formula_ast.args[0].params = {"window": 60}
    assert build().sha256 != build(spec=other).sha256


def test_the_hash_does_not_depend_on_dict_insertion_order() -> None:
    reordered = plan()
    step = reordered.steps[0]
    step.arguments = dict(reversed(list(step.arguments.items())))
    assert build().sha256 == build(plan=reordered).sha256


def test_no_timestamp_leaks_into_the_manifest() -> None:
    """A timestamp would make every rebuild a different manifest."""
    manifest = build()
    assert "created_at" not in manifest.canonical_json()
    assert "timestamp" not in manifest.canonical_json()


# --------------------------------------------------------------------------- content


def test_manifest_carries_the_time_convention(build_manifest=build) -> None:
    assert build_manifest().time_convention is not None


def test_every_input_artifact_carries_a_hash() -> None:
    manifest = build()
    assert manifest.input_artifacts
    assert all(artifact.sha256 for artifact in manifest.input_artifacts)


def test_manifest_records_the_confirmed_field_bindings() -> None:
    manifest = build()
    assert manifest.field_selections[0].field == "s_dq_adjclose"


# --------------------------------------------------------------------------- fail closed


def test_an_unregistered_tool_is_refused() -> None:
    """A plan that grew an unexpected step must not become executable."""
    with pytest.raises(ManifestSchemaError, match="arbitrary_sql"):
        build(plan=plan(tool="wind.arbitrary_sql"))


def test_an_input_artifact_without_a_hash_is_refused() -> None:
    with pytest.raises(ManifestSchemaError, match="sha256"):
        build(inputs=[InputArtifact(uri="file:///x.parquet", sha256="", rows=1)])


def test_a_formula_that_fails_ast_checks_is_refused() -> None:
    bad = spec()
    bad.formula_ast.args[0].args[0].name = "unbound_variable"
    with pytest.raises(ManifestSchemaError):
        build(spec=bad)


def test_a_plan_with_no_steps_is_refused() -> None:
    with pytest.raises(ManifestSchemaError, match="step"):
        build(plan=ExecutionPlan(steps=[]))


# --------------------------------------------------------------------------- signing


def test_a_signed_manifest_verifies() -> None:
    signed = sign(build(), key=SIGNING_KEY)
    assert verify(signed, key=SIGNING_KEY).sha256 == build().sha256


def test_a_tampered_payload_fails_verification() -> None:
    """The attack this exists for: widen the date range after approval."""
    signed = sign(build(), key=SIGNING_KEY)
    tampered = signed.model_copy(
        update={"payload": signed.payload.replace("2024-06-30", "2099-01-01")}
    )
    with pytest.raises(ManifestVerificationError):
        verify(tampered, key=SIGNING_KEY)


def test_a_wrong_key_fails_verification() -> None:
    signed = sign(build(), key=SIGNING_KEY)
    with pytest.raises(ManifestVerificationError):
        verify(signed, key="a-different-key")  # pragma: allowlist secret


def test_the_signature_is_not_the_hash() -> None:
    """A hash proves integrity; only a keyed signature proves origin."""
    signed = sign(build(), key=SIGNING_KEY)
    assert signed.signature != build().sha256
