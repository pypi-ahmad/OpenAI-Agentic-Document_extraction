from unittest.mock import Mock

import pytest

from ade_app.cost import TokenUsage
from ade_app.hybrid import HybridPageExtractor
from ade_app.layout import LayoutAnalysis, LayoutRegion
from ade_app.models import Box, SegmentPatchAudit, SemanticSegmentPatch
from ade_app.openai_client import OpenAIPageExtractor, RegionReadResult, _record_failed_usage
from ade_app.preprocessing import PageTransform, PreparedPage
from ade_app.raster import RenderedPage
from ade_app.spending import SpendingStopped


def _layout_inputs():
    page = RenderedPage(1, b"unused", 100, 100)
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    prepared = PreparedPage(
        page, page, PageTransform(forward=identity, inverse=identity, operations=[])
    )
    box = Box(xmin=0, ymin=0, xmax=1, ymax=1)
    analysis = LayoutAnalysis(
        source_page=1,
        regions=[
            LayoutRegion(
                region_id="r0",
                kind="text",
                confidence=0.99,
                box=box,
                prepared_box=box,
                route="local_text",
                text="Text",
            )
        ],
        proposals=[],
    )
    return prepared, analysis


def test_regional_assembly_failure_preserves_paid_work(monkeypatch, audited_semantic_page):
    prepared, analysis = _layout_inputs()
    patch = SemanticSegmentPatch(
        segment_id="r0",
        element=audited_semantic_page.children[0],
        audit=SegmentPatchAudit.model_validate(audited_semantic_page.audits[0].model_dump()),
    )
    usage = TokenUsage(input_tokens=100, output_tokens=20)
    result = RegionReadResult({"r0": patch}, (), usage, "response", "request", "standard", 2, 1)
    llm = OpenAIPageExtractor(object(), profile_path=None)
    monkeypatch.setattr(llm, "_read_region_items", Mock(return_value=result))
    monkeypatch.setattr(
        "ade_app.openai_client.validate_page_extraction",
        Mock(side_effect=ValueError("assembly failed")),
    )
    with pytest.raises(ValueError, match="assembly failed") as caught:
        llm.extract_regions(prepared, analysis, job_id="job", page_count=1)
    assert getattr(caught.value, "usage", None) == usage
    assert getattr(caught.value, "api_call_count", None) == 2
    assert getattr(caught.value, "retry_count", None) == 1


def test_budget_stopped_fallback_preserves_prior_regional_usage(monkeypatch):
    prepared, analysis = _layout_inputs()
    usage = TokenUsage(input_tokens=100, output_tokens=20)
    regional = ValueError("assembly failed")
    _record_failed_usage(regional, (("gpt-5.6-terra", usage),), 3, 1)
    llm = OpenAIPageExtractor(object(), profile_path=None)
    monkeypatch.setattr(llm, "extract_regions", Mock(side_effect=regional))
    monkeypatch.setattr(llm, "extract_primary", Mock(side_effect=SpendingStopped("budget")))
    monkeypatch.setattr("ade_app.hybrid.uncovered_foreground", lambda *args: ())
    extractor = HybridPageExtractor(llm, calibrated_routes=set())
    with pytest.raises(SpendingStopped) as caught:
        extractor.extract_primary_from_layout(
            prepared.original, prepared, analysis, job_id="job", page_count=1
        )
    assert caught.value.usage == usage
    assert caught.value.api_call_count == 3
    assert caught.value.retry_count == 1
