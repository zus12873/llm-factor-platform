"""The factor library: immutable published versions.

Publishing copies the artifact into a versioned directory rather than referencing
where it already sits. The job queue's retention policy sweeps run artifacts on a
schedule, and a library entry pointing at a swept file is a saved factor that
opens to nothing months later. A copy costs disk; a dangling reference costs the
user's trust in everything else in the library.

Immutability is enforced, not documented. Editing a published version would
change what a saved link means without changing its identity — anyone who cited
version 3 last month would now be citing something else, with no way to notice.
Corrections publish version N+1.

The review gate lives here too: a factor built on a `disputed` metric cannot be
published at all, and one built on an `unreviewed` metric publishes carrying that
label. The label has to travel with the entry, because by the time someone reads
a library listing, the context that produced it is gone.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from factor_platform.domain.errors import DomainError
from factor_platform.domain.models import FactorSpec
from factor_platform.factor.metric_registry import MetricRegistry, ReviewStatus
from factor_platform.library.provenance import ProvenanceRecord


class ImmutableArtifactError(DomainError):
    """Raised when a published version is edited instead of superseded."""


class PublishRefusedError(DomainError):
    """Raised when a factor may not enter the library."""


class LibraryEntry(BaseModel):
    """One immutable published factor version."""

    schema_version: int = 1
    factor_id: str
    version: int
    session_id: str
    factor_name: str
    spec: FactorSpec
    manifest_sha256: str
    program_sha256: str
    result_sha256: str
    artifact_path: str
    created_by: str = ""
    created_at: str = ""
    review_status: str = ReviewStatus.UNREVIEWED.value
    review_note: str = ""
    provenance: ProvenanceRecord | None = None
    metric_keys: list[str] = Field(default_factory=list)


class FactorLibrary:
    """Publishes and reads immutable factor versions on the filesystem."""

    def __init__(
        self,
        root: Path | str,
        *,
        registry: MetricRegistry | None = None,
    ) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._registry = registry or MetricRegistry.load()

    # ------------------------------------------------------------------ publish

    def publish(
        self,
        *,
        factor_id: str,
        session_id: str,
        spec: FactorSpec,
        manifest_sha256: str,
        result_artifact: Path,
        program_source: str,
        provenance: ProvenanceRecord | None = None,
        metric_keys: list[str] | None = None,
        created_by: str = "",
        created_at: str = "",
    ) -> LibraryEntry:
        """Publish the next version of ``factor_id``."""
        keys = metric_keys or []
        review_status, review_note = self._review_gate(keys)

        version = self.next_version(factor_id)
        version_dir = self._version_dir(factor_id, version)
        version_dir.mkdir(parents=True, exist_ok=True)

        # Copy, never reference: the retention sweep will eventually remove the
        # run artifact, and a library entry must not depend on that not happening.
        stored = version_dir / "result.parquet"
        shutil.copy2(result_artifact, stored)

        entry = LibraryEntry(
            factor_id=factor_id,
            version=version,
            session_id=session_id,
            factor_name=spec.factor_name,
            spec=spec,
            manifest_sha256=manifest_sha256,
            program_sha256=hashlib.sha256(program_source.encode("utf-8")).hexdigest(),
            result_sha256=_sha256(stored),
            artifact_path=str(stored.relative_to(self._root)),
            created_by=created_by,
            created_at=created_at,
            review_status=review_status,
            review_note=review_note,
            provenance=provenance,
            metric_keys=keys,
        )
        (version_dir / "entry.json").write_text(
            entry.model_dump_json(indent=2), encoding="utf-8"
        )
        return entry

    def _review_gate(self, metric_keys: list[str]) -> tuple[str, str]:
        """Refuse disputed metrics; label unreviewed ones.

        The label is stored rather than computed at read time: by the time
        someone browses the library, the session that produced the entry is gone,
        and a status recomputed from a registry that has since changed would
        describe a different thing.
        """
        notes: list[str] = []
        status = ReviewStatus.REVIEWED.value

        for key in metric_keys:
            verdict = self._registry.gate(key)
            if not verdict.allowed:
                raise PublishRefusedError(
                    f"因子引用了不可发布的口径 {key}：{verdict.reason}"
                )
            definition = self._registry.get(key)
            if definition and definition.review_status is ReviewStatus.UNREVIEWED:
                status = ReviewStatus.UNREVIEWED.value
                notes.append(f"{key} 未复核")

        if not metric_keys:
            status = ReviewStatus.UNREVIEWED.value
            notes.append("未声明口径，视为未复核")

        return status, "；".join(notes)

    # ------------------------------------------------------------------ reads

    def next_version(self, factor_id: str) -> int:
        return len(self.list_versions(factor_id)) + 1

    def list_versions(self, factor_id: str) -> list[int]:
        base = self._root / factor_id
        if not base.exists():
            return []
        return sorted(
            int(path.name[1:])
            for path in base.iterdir()
            if path.is_dir() and path.name.startswith("v") and path.name[1:].isdigit()
        )

    def get_version(self, factor_id: str, version: int) -> LibraryEntry:
        path = self._version_dir(factor_id, version) / "entry.json"
        if not path.exists():
            raise KeyError(f"unknown factor version: {factor_id} v{version}")
        return LibraryEntry.model_validate_json(path.read_text(encoding="utf-8"))

    def list_factors(self) -> list[LibraryEntry]:
        """Latest version of every factor, newest version first within a factor."""
        entries: list[LibraryEntry] = []
        for base in sorted(p for p in self._root.iterdir() if p.is_dir()):
            versions = self.list_versions(base.name)
            if versions:
                entries.append(self.get_version(base.name, versions[-1]))
        return entries

    # ------------------------------------------------------------------ immutability

    def replace(self, factor_id: str, version: int, spec: FactorSpec) -> LibraryEntry:
        """Always refuses.

        Present so the refusal is explicit at the call site rather than a missing
        method someone works around. A correction publishes N+1; editing in place
        would change what an existing citation means without changing its
        identity.
        """
        del spec
        raise ImmutableArtifactError(
            f"{factor_id} v{version} 已发布，不可修改。"
            "修正请发布新版本——就地编辑会让已有引用指向不同的东西，"
            "而引用者无从察觉。"
        )

    def verify_artifact(self, factor_id: str, version: int) -> bool:
        """Whether the stored artifact still matches the hash recorded for it."""
        entry = self.get_version(factor_id, version)
        return _sha256(self._root / entry.artifact_path) == entry.result_sha256

    def _version_dir(self, factor_id: str, version: int) -> Path:
        return self._root / factor_id / f"v{version}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def as_json(entry: LibraryEntry) -> dict[str, Any]:
    return json.loads(entry.model_dump_json())


__all__ = [
    "FactorLibrary",
    "ImmutableArtifactError",
    "LibraryEntry",
    "PublishRefusedError",
]
