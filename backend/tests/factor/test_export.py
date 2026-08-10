"""Tests for the user-facing code export.

``factor.py`` is a deliverable, not a runtime artifact. It exists so a researcher
can read what was computed, copy it into their own environment, and reproduce the
result without this platform. Nothing internal executes it — which is exactly why
it needs no source AST whitelist.

The one property it must have is traceability: the exported file has to name the
manifest it came from, or a reviewer holding the file cannot tell which run it
describes.
"""

from __future__ import annotations

import ast

from factor_platform.factor.export import CodeExporter
from tests.execution.test_manifest import build


def test_export_is_deterministic() -> None:
    manifest = build()
    assert CodeExporter().render(manifest).source == CodeExporter().render(manifest).source


def test_export_names_the_manifest_it_came_from() -> None:
    """A file without its manifest hash cannot be traced back to a run."""
    manifest = build()
    exported = CodeExporter().render(manifest)
    assert manifest.sha256 in exported.source


def test_export_is_valid_python() -> None:
    """It is meant to be read and run by a human, so it must at least parse."""
    ast.parse(CodeExporter().render(build()).source)


def test_export_carries_its_own_hash() -> None:
    exported = CodeExporter().render(build())
    assert len(exported.sha256) == 64


def test_export_states_the_canonical_formula_and_time_convention() -> None:
    """The two things a reviewer must check before trusting the numbers."""
    manifest = build()
    source = CodeExporter().render(manifest).source
    assert manifest.factor_spec.canonical_formula in source
    assert manifest.time_convention.trade_date in source


def test_export_lists_the_confirmed_field_bindings() -> None:
    source = CodeExporter().render(build()).source
    assert "ashareeodprices" in source
    assert "s_dq_adjclose" in source


def test_export_says_it_is_not_the_executed_artifact() -> None:
    """A reader who assumes this file ran would draw the wrong conclusions."""
    source = CodeExporter().render(build()).source
    assert "manifest" in source.lower()
    assert "not" in source.lower()


def test_export_contains_no_credentials_or_connection_details() -> None:
    source = CodeExporter().render(build()).source.lower()
    for forbidden in ("password", "api_key", "mysql+pymysql://", "secret"):
        assert forbidden not in source
