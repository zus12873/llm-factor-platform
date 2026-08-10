"""Tests for the metric definition registry.

Metric definitions used to live in three places at once: hard-coded tuples in the
clarification rules, the Wind alias YAML, and the golden-case fixtures. None of
those can be read and signed off by someone who does not write Python — which
matters here, because this project has no full-time researcher and the metric
definitions are exactly what the supervising advisor must sample-check.

So the registry is a single YAML file, and it carries a three-value review state.
The middle value is the point: ``unreviewed`` is usable for prototyping but must
be labelled, and ``disputed`` blocks outright. A known-wrong mapping degrading to
a warning is how a wrong number reaches a published factor.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factor_platform.domain.errors import DisputedMetricError
from factor_platform.factor.metric_registry import (
    MetricRegistry,
    ReviewStatus,
)


@pytest.fixture(scope="module")
def registry() -> MetricRegistry:
    return MetricRegistry.load()


# --------------------------------------------------------------------------- content


def test_registry_covers_the_first_release_metrics(registry: MetricRegistry) -> None:
    for key in (
        "ROE_TTM",
        "ROA_TTM",
        "CFO_TO_PROFIT",
        "PE_TTM",
        "PB",
        "PS_TTM",
        "REVENUE_YOY",
        "NET_PROFIT_YOY",
        "OPERATING_PROFIT_YOY",
    ):
        assert registry.get(key) is not None, f"{key} missing from the registry"


def test_registry_covers_every_clarification_option(registry: MetricRegistry) -> None:
    """An option the user can pick must have a definition behind it."""
    for category in ("profitability", "valuation", "growth"):
        options = registry.options_for(category)
        assert options, f"no options registered for {category}"
        for option in options:
            assert registry.get(option) is not None, f"{option} offered but undefined"


def test_every_definition_carries_the_fields_a_reviewer_needs(
    registry: MetricRegistry,
) -> None:
    """A reviewer cannot sign off on a name; they need the mapping and the units."""
    for definition in registry.all():
        assert definition.display_zh
        assert definition.definition
        assert definition.wind_table
        assert definition.wind_field
        assert definition.unit is not None


def test_first_release_metrics_start_unreviewed(registry: MetricRegistry) -> None:
    """Nothing may claim review it has not had."""
    assert registry.get("ROE_TTM").review_status is ReviewStatus.UNREVIEWED


# --------------------------------------------------------------------------- the gate


def test_unreviewed_metric_is_allowed_but_flagged(registry: MetricRegistry) -> None:
    verdict = registry.gate("ROE_TTM")
    assert verdict.allowed is True
    assert verdict.requires_warning is True
    assert "未复核" in verdict.reason


def test_reviewed_metric_passes_without_a_warning() -> None:
    registry = MetricRegistry.from_mapping(
        {
            "TEST_OK": {
                "display_zh": "测试口径",
                "definition": "d",
                "category": "profitability",
                "wind_table": "t",
                "wind_field": "f",
                "unit": "ratio",
                "review_status": "reviewed",
                "reviewer": "advisor",
                "reviewed_at": "2026-08-10",
            }
        }
    )
    verdict = registry.gate("TEST_OK")
    assert verdict.allowed is True
    assert verdict.requires_warning is False


def test_disputed_metric_is_refused(registry: MetricRegistry) -> None:
    """The two known-wrong mappings must not be usable at all."""
    verdict = registry.gate("FLOAT_MV")
    assert verdict.allowed is False
    assert verdict.requires_warning is True


def test_disputed_metric_raises_when_enforced(registry: MetricRegistry) -> None:
    with pytest.raises(DisputedMetricError, match="流通市值"):
        registry.enforce("FLOAT_MV")


def test_enforcing_an_unreviewed_metric_does_not_raise(registry: MetricRegistry) -> None:
    registry.enforce("ROE_TTM")


def test_the_two_known_bad_mappings_are_registered_as_disputed(
    registry: MetricRegistry,
) -> None:
    """Both are plausible-looking column names that mean something else entirely.

    ``float_a_shr`` is a *share count*, not a market value; ``net_profit`` in the
    cash-flow statement is the indirect-method starting line, not operating cash
    flow. Either one silently produces a factor that looks reasonable.
    """
    for key in ("FLOAT_MV", "CFO_WRONG_MAPPING"):
        definition = registry.get(key)
        assert definition is not None, f"{key} must be registered so it can be blocked"
        assert definition.review_status is ReviewStatus.DISPUTED
        assert definition.review_comment, "a disputed entry must say what is wrong"


def test_an_unknown_key_is_not_silently_allowed(registry: MetricRegistry) -> None:
    verdict = registry.gate("NO_SUCH_METRIC")
    assert verdict.allowed is False


# --------------------------------------------------------------------------- ranges


def test_plausible_range_is_available_for_magnitude_checks(
    registry: MetricRegistry,
) -> None:
    """Compensates for having no researcher: the number must look like itself."""
    low, high = registry.plausible_range("ROE_TTM")
    assert low < high


def test_plausible_range_of_an_unknown_metric_is_none(registry: MetricRegistry) -> None:
    assert registry.plausible_range("NO_SUCH_METRIC") is None


# --------------------------------------------------------------------------- file shape


def test_registry_file_is_plain_yaml_a_reviewer_can_edit() -> None:
    path = Path(MetricRegistry.default_path())
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "review_status" in text
    assert "reviewer" in text
