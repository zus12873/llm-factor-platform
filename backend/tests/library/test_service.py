"""Tests for the factor library.

Three properties, each guarding a failure that only shows up months later:

* **Copy, don't reference.** The retention sweep removes run artifacts. A library
  entry pointing at one opens to nothing long after anyone remembers why.
* **Immutable versions.** Editing in place changes what an existing citation
  means without changing its identity, so the person who cited v3 has no way to
  notice they are now reading something else.
* **The review label is stored, not recomputed.** By the time someone browses the
  library the registry may have moved on, and a recomputed status would describe
  a different thing than the one that was published.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factor_platform.domain.models import FactorSpec
from factor_platform.library.provenance import (
    ComponentVersions,
    InputProvenance,
    ProvenanceRecord,
)
from factor_platform.library.service import (
    FactorLibrary,
    ImmutableArtifactError,
    PublishRefusedError,
)


def spec(name: str = "momentum") -> FactorSpec:
    return FactorSpec.model_validate(
        {
            "factor_name": name,
            "asset_type": "stock",
            "universe": "000300.SH",
            "frequency": "daily",
            "direction": "higher_is_better",
            "canonical_formula": "rank(roe_ttm)",
            "formula_ast": {
                "type": "call",
                "op": "rank",
                "args": [{"type": "variable", "name": "roe_ttm"}],
            },
            "variables": [{"logical_name": "roe_ttm", "meaning": "ROE"}],
        }
    )


@pytest.fixture
def artifact(tmp_path: Path) -> Path:
    path = tmp_path / "run" / "result.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"PAR1-result-bytes")
    return path


@pytest.fixture
def library(tmp_path: Path) -> FactorLibrary:
    return FactorLibrary(tmp_path / "library")


def publish(library: FactorLibrary, artifact: Path, **overrides: object):
    payload: dict = {
        "factor_id": "quality",
        "session_id": "s1",
        "spec": spec(),
        "manifest_sha256": "m" * 64,
        "result_artifact": artifact,
        "program_source": "print('factor')",
        "metric_keys": ["ROE_TTM"],
    }
    payload.update(overrides)
    return library.publish(**payload)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- publishing


def test_publishing_records_stable_hashes(library: FactorLibrary, artifact: Path) -> None:
    entry = publish(library, artifact)
    assert len(entry.program_sha256) == 64
    assert len(entry.result_sha256) == 64
    assert entry.version == 1


def test_the_artifact_is_copied_not_referenced(
    library: FactorLibrary, artifact: Path, tmp_path: Path
) -> None:
    """The retention sweep will remove the run artifact; the entry must survive."""
    entry = publish(library, artifact)
    artifact.unlink()

    stored = tmp_path / "library" / entry.artifact_path
    assert stored.exists()
    assert library.verify_artifact("quality", 1) is True


def test_republishing_creates_the_next_version(
    library: FactorLibrary, artifact: Path
) -> None:
    assert publish(library, artifact).version == 1
    assert publish(library, artifact, spec=spec("momentum_v2")).version == 2
    assert library.list_versions("quality") == [1, 2]


def test_an_older_version_stays_readable_after_a_newer_one(
    library: FactorLibrary, artifact: Path
) -> None:
    publish(library, artifact)
    publish(library, artifact, spec=spec("momentum_v2"))
    assert library.get_version("quality", 1).spec.factor_name == "momentum"
    assert library.get_version("quality", 2).spec.factor_name == "momentum_v2"


def test_listing_returns_the_latest_version_of_each_factor(
    library: FactorLibrary, artifact: Path
) -> None:
    publish(library, artifact)
    publish(library, artifact, spec=spec("momentum_v2"))
    publish(library, artifact, factor_id="value")
    listed = {entry.factor_id: entry.version for entry in library.list_factors()}
    assert listed == {"quality": 2, "value": 1}


# --------------------------------------------------------------------------- immutability


def test_a_published_version_cannot_be_edited(
    library: FactorLibrary, artifact: Path
) -> None:
    """An edit changes what an existing citation means, invisibly."""
    entry = publish(library, artifact)
    with pytest.raises(ImmutableArtifactError, match="发布新版本"):
        library.replace(entry.factor_id, entry.version, spec("changed"))


def test_a_corrupted_artifact_is_detected(
    library: FactorLibrary, artifact: Path, tmp_path: Path
) -> None:
    entry = publish(library, artifact)
    (tmp_path / "library" / entry.artifact_path).write_bytes(b"tampered")
    assert library.verify_artifact("quality", 1) is False


# --------------------------------------------------------------------------- review gate


def test_a_disputed_metric_cannot_be_published(
    library: FactorLibrary, artifact: Path
) -> None:
    with pytest.raises(PublishRefusedError, match="FLOAT_MV"):
        publish(library, artifact, metric_keys=["FLOAT_MV"])


def test_an_unreviewed_metric_publishes_carrying_its_label(
    library: FactorLibrary, artifact: Path
) -> None:
    """The label must travel with the entry; the context that produced it is gone."""
    entry = publish(library, artifact, metric_keys=["ROE_TTM"])
    assert entry.review_status == "unreviewed"
    assert "ROE_TTM" in entry.review_note


def test_declaring_no_metric_is_treated_as_unreviewed(
    library: FactorLibrary, artifact: Path
) -> None:
    """Silence is not a clean bill of health."""
    entry = publish(library, artifact, metric_keys=[])
    assert entry.review_status == "unreviewed"


# --------------------------------------------------------------------------- provenance


def provenance(*, artifact_hash: str = "a" * 64, commit: str = "abc123") -> ProvenanceRecord:
    return ProvenanceRecord(
        manifest_sha256="m" * 64,
        result_sha256="r" * 64,
        inputs=[
            InputProvenance(
                input_artifact_sha256=artifact_hash,
                query_timestamp="2024-07-01T09:00:00Z",
                source_database="wind",
                source_table="asharettmhis",
                source_fields=["s_fa_roe_ttm"],
                row_count=1000,
                input_non_null_ratio=0.98,
            )
        ],
        versions=ComponentVersions(code_commit=commit),
    )


def test_provenance_is_stored_with_the_entry(
    library: FactorLibrary, artifact: Path
) -> None:
    publish(library, artifact, provenance=provenance())
    reloaded = library.get_version("quality", 1)
    assert reloaded.provenance is not None
    assert reloaded.provenance.inputs[0].source_table == "asharettmhis"


def test_a_restated_input_is_named_as_the_reason_for_a_difference() -> None:
    """Wind restates financials; the identical program then returns different values.

    Without this, the person investigating starts by suspecting the code.
    """
    reasons = provenance(artifact_hash="a" * 64).explains_difference_from(
        provenance(artifact_hash="b" * 64)
    )
    assert any("修订" in reason for reason in reasons)


def test_a_code_change_is_named_as_the_reason() -> None:
    reasons = provenance(commit="abc123").explains_difference_from(
        provenance(commit="def456")
    )
    assert any("code_commit" in reason for reason in reasons)


def test_an_unexplained_difference_is_reported_as_a_defect() -> None:
    """Everything recorded matches and the numbers differ: something unrecorded moved."""
    left = provenance()
    right = provenance().model_copy(update={"result_sha256": "z" * 64})
    reasons = left.explains_difference_from(right)
    assert any("未被记录" in reason for reason in reasons)


def test_identical_records_explain_nothing() -> None:
    assert provenance().explains_difference_from(provenance()) == []
