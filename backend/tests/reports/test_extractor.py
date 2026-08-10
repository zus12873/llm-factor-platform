"""Tests for report factor extraction.

The three properties under test all guard against an extraction that *looks*
correct:

* Only bounded excerpts cross boundary B4 — the report body never does.
* A citation to an evidence id that was not in the input is rejected. A model
  asked to cite its source will invent one, and an invented citation is worse
  than none because it reads as verified.
* Low confidence or an unreliable page layout forces manual confirmation. A
  formula lifted from a two-column page parses fine and is wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factor_platform.llm.base import FakeLLMProvider
from factor_platform.reports.extractor import (
    CONFIDENCE_THRESHOLD,
    MAX_EXCERPTS,
    FormulaExtractionStatus,
    ReportExtractor,
    score_blocks,
)
from factor_platform.reports.pdf import ParsedPage, ParsedReport, TextBlock

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "reports"


def block(text: str) -> TextBlock:
    return TextBlock(text=text, bbox=(0.0, 0.0, 100.0, 20.0))


def report(*, layout_flags: list[str] | None = None) -> ParsedReport:
    return ParsedReport(
        filename="note.pdf",
        page_count=2,
        pages=[
            ParsedPage(
                page_number=1,
                text="",
                blocks=[
                    block("本文提出以净资产收益率构建盈利因子。"),
                    block("因子定义：对 ROE_TTM 做横截面排名。"),
                    block("致谢与免责声明。"),
                ],
                width=595,
                height=842,
                text_density=2.0,
                layout_flags=layout_flags or [],
            ),
            ParsedPage(
                page_number=2,
                text="",
                blocks=[block("回测样本为沪深300成分股，调仓频率为月度。")],
                width=595,
                height=842,
                text_density=2.0,
            ),
        ],
    )


def draft(**overrides: object) -> str:
    payload = {
        "factor_name": "quality",
        "hypothesis": "ROE 高的股票收益更好",
        "confidence": 0.9,
        "extracted_formula_text": "rank(ROE_TTM)",
        "variables": [{"logical_name": "roe_ttm", "meaning": "净资产收益率"}],
        "cited_evidence_ids": ["p1b1"],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def extractor(content: str | None = None, **kwargs: object) -> ReportExtractor:
    provider = FakeLLMProvider()
    if content is not None:
        provider.enqueue_content(content)
    return ReportExtractor(provider, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- scoring


def test_only_relevant_blocks_are_selected() -> None:
    """The disclaimer must not be one of the few things that crosses B4."""
    excerpts = score_blocks(report())
    texts = [excerpt.text for excerpt in excerpts]
    assert any("因子定义" in text for text in texts)
    assert not any("免责声明" in text for text in texts)


def test_the_number_of_excerpts_is_bounded() -> None:
    """A cap, not a tuning knob: it bounds what leaves regardless of report size."""
    long_report = ParsedReport(
        filename="long.pdf",
        page_count=1,
        pages=[
            ParsedPage(
                page_number=1,
                text="",
                blocks=[block(f"因子定义 {i} 横截面排名") for i in range(50)],
            )
        ],
    )
    assert len(score_blocks(long_report)) <= MAX_EXCERPTS


def test_every_excerpt_carries_its_page_number() -> None:
    for excerpt in score_blocks(report()):
        assert excerpt.page_number in {1, 2}
        assert excerpt.evidence_id.startswith("p")


def test_scoring_is_deterministic() -> None:
    first = [e.evidence_id for e in score_blocks(report())]
    second = [e.evidence_id for e in score_blocks(report())]
    assert first == second


# --------------------------------------------------------------------------- happy path


async def test_a_confident_extraction_carries_its_evidence() -> None:
    result = await extractor(draft()).extract(report())
    assert result.formula_extraction.status is FormulaExtractionStatus.EXTRACTED
    assert result.formula_extraction.source_pages == [1]
    assert result.evidence
    assert result.evidence[0].evidence_id == "p1b1"


async def test_the_extraction_records_what_it_read() -> None:
    result = await extractor(draft()).extract(report())
    assert result.formula_extraction.extracted_text == "rank(ROE_TTM)"


# --------------------------------------------------------------------------- refusals


async def test_an_invented_evidence_id_is_rejected() -> None:
    """An invented citation reads as verified, which makes it worse than none."""
    result = await extractor(draft(cited_evidence_ids=["p9b9"])).extract(report())
    assert result.formula_extraction.status is FormulaExtractionStatus.NEEDS_MANUAL_CONFIRMATION
    assert "不存在的证据" in result.formula_extraction.warning


async def test_low_confidence_forces_manual_confirmation() -> None:
    result = await extractor(draft(confidence=CONFIDENCE_THRESHOLD - 0.1)).extract(report())
    assert result.formula_extraction.status is FormulaExtractionStatus.NEEDS_MANUAL_CONFIRMATION
    assert "置信度" in result.formula_extraction.warning


async def test_a_multi_column_page_forces_manual_confirmation() -> None:
    """Interleaved columns read out of order; the result still parses."""
    result = await extractor(draft()).extract(report(layout_flags=["multi_column"]))
    assert result.formula_extraction.status is FormulaExtractionStatus.NEEDS_MANUAL_CONFIRMATION
    assert "多栏" in result.formula_extraction.warning


async def test_an_image_page_forces_manual_confirmation() -> None:
    result = await extractor(draft()).extract(report(layout_flags=["has_image"]))
    assert result.formula_extraction.status is FormulaExtractionStatus.NEEDS_MANUAL_CONFIRMATION


async def test_a_report_with_no_relevant_block_asks_for_manual_input() -> None:
    empty = ParsedReport(
        filename="x.pdf",
        page_count=1,
        pages=[ParsedPage(page_number=1, text="", blocks=[block("目录")])],
    )
    result = await extractor(draft()).extract(empty)
    assert result.formula_extraction.status is FormulaExtractionStatus.NEEDS_MANUAL_CONFIRMATION


async def test_a_model_failure_becomes_manual_confirmation_not_a_crash() -> None:
    """The user gets the evidence and does it by hand; the upload is not lost."""
    result = await extractor("not json at all").extract(report())
    assert result.formula_extraction.status is FormulaExtractionStatus.NEEDS_MANUAL_CONFIRMATION
    assert result.evidence


# --------------------------------------------------------------------------- boundary B4


async def test_local_only_mode_never_calls_the_model() -> None:
    """It still produces the evidence; it just refuses to ask what it means."""
    provider = FakeLLMProvider()
    result = await ReportExtractor(provider, local_only_mode=True).extract(report())
    assert result.formula_extraction.status is FormulaExtractionStatus.NEEDS_MANUAL_CONFIRMATION
    assert "全本地模式" in result.formula_extraction.warning
    assert result.evidence, "local mode must still surface the citable blocks"


async def test_the_full_report_body_is_never_sent() -> None:
    """B4: excerpts may cross, the report may not."""
    excerpts = score_blocks(report())
    total = sum(len(excerpt.text) for excerpt in excerpts)
    full = sum(len(b.text) for page in report().pages for b in page.blocks)
    assert len(excerpts) < sum(len(page.blocks) for page in report().pages)
    assert total < full


async def test_extraction_never_invents_a_rebalance_frequency() -> None:
    """Unstated in the report means unset in the draft, not guessed."""
    result = await extractor(
        draft(variables=[{"logical_name": "roe_ttm", "meaning": "净资产收益率"}])
    ).extract(report())
    assert all("rebalance" not in variable["logical_name"] for variable in result.variables)


@pytest.mark.parametrize("flag", ["multi_column", "has_image"])
async def test_unreliable_layouts_never_reach_execution(flag: str) -> None:
    """The rule: the platform does not execute a formula it is unsure it read."""
    result = await extractor(draft(confidence=0.99)).extract(report(layout_flags=[flag]))
    assert result.formula_extraction.status is FormulaExtractionStatus.NEEDS_MANUAL_CONFIRMATION
