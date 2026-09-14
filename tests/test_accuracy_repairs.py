from unittest.mock import Mock

import pytest

from ade_app.consensus import apply_resolutions, compare_elements
from ade_app.fields import _segment_evidence, public_fields
from ade_app.hybrid import HybridPageExtractor
from ade_app.layout import LayoutAnalysis, LayoutRegion
from ade_app.models import (
    Box,
    ExtractedField,
    FieldEvidence,
    FieldResolution,
    SemanticCheckbox,
    SemanticLeaf,
    SemanticLineValue,
    SemanticText,
)
from ade_app.openai_client import OpenAIPageExtractor, _redact_unverified_element, _region_inputs
from ade_app.preprocessing import PageTransform, PreparedPage
from ade_app.raster import RenderedPage
from ade_app.services.confidence import build_confidence_report


def test_missing_evidence_cannot_be_accepted() -> None:
    assert _segment_evidence(None, 0) == ("terra", 0.0, ("missing_evidence",))


def test_public_uncertainty_is_null_not_a_candidate() -> None:
    field = ExtractedField(
        field_id="f0",
        original_name="ID",
        canonical_name="id",
        value="candidate",
        confidence=99,
        status="accepted",
        reasons=[],
        validation_checks=[],
        evidence=[
            FieldEvidence(
                page=1,
                region_id="text-0",
                box=Box(xmin=0, ymin=0, xmax=1, ymax=1),
                route="luna",
                candidate="candidate",
                confidence=99,
            )
        ],
    )
    result = public_fields([field])[0]
    assert result.value is None
    assert result.confidence is None
    assert result.status == "needs_review"
    assert result.evidence[0].candidate == "candidate"


def test_ambiguous_checkbox_is_not_false() -> None:
    assert SemanticCheckbox(checked=None).checked is None
    with pytest.raises(ValueError):
        SemanticCheckbox.model_validate({"checked": "false"})


def test_redaction_keeps_agreeing_neighbors(audited_semantic_page) -> None:
    element = audited_semantic_page.children[0].model_copy(deep=True)
    neighbor = element.lines[0].model_copy(deep=True)
    neighbor.content = [SemanticText(text="Verified neighbor")]
    element.lines.append(neighbor)
    result = _redact_unverified_element(element, ("line-0",))
    assert isinstance(result, SemanticLeaf)
    assert isinstance(result.lines[0].content[0], SemanticText)
    assert isinstance(result.lines[1].content[0], SemanticText)
    assert result.lines[0].content[0].text == "[UNVERIFIED]"
    assert result.lines[1].content[0].text == "Verified neighbor"


def test_supported_correction_survives_but_third_value_does_not(audited_semantic_page) -> None:
    primary = audited_semantic_page.children[0]
    assert isinstance(primary, SemanticLeaf)
    independent = primary.model_copy(deep=True)
    independent.lines[0].content = [SemanticText(text="Corrected")]
    disputes = compare_elements(primary, independent).disagreements
    resolution = FieldResolution(
        field_id="line-0",
        kind="line",
        status="resolved",
        line=SemanticLineValue(
            content=[*independent.lines[0].content], style=independent.lines[0].style
        ),
        cell=None,
    )
    corrected, unresolved = apply_resolutions(primary, disputes, [resolution])
    assert not unresolved
    assert isinstance(corrected, SemanticLeaf)
    assert isinstance(corrected.lines[0].content[0], SemanticText)
    assert corrected.lines[0].content[0].text == "Corrected"
    assert resolution.line is not None
    resolution.line.content = [SemanticText(text="Invented third value")]
    unchanged, unresolved = apply_resolutions(primary, disputes, [resolution])
    assert unresolved == ("line-0",)
    assert unchanged == primary


def test_local_gate_precedes_real_regional_interface() -> None:
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
                text="Printed text",
            )
        ],
        proposals=[],
    )
    llm = OpenAIPageExtractor(object(), profile_path=None)
    extractor = HybridPageExtractor(llm, calibrated_routes={"local_text"})
    primary = extractor.extract_primary_from_layout(
        page, prepared, analysis, job_id="j", page_count=1
    )
    assert primary.attempts == 0
    assert primary.source_model == "PP-StructureV3"


def test_unverified_local_table_must_use_visual_model() -> None:
    box = Box(xmin=0, ymin=0, xmax=1, ymax=1)
    analysis = LayoutAnalysis(
        source_page=1,
        regions=[
            LayoutRegion(
                region_id="r0",
                kind="table",
                confidence=0.99,
                box=box,
                prepared_box=box,
                route="local_table",
                html="<table><tr><td>A</td></tr></table>",
                markdown="|A|\n|---|",
                table_is_simple=True,
            )
        ],
        proposals=[],
    )
    assert _region_inputs(analysis)[0].route == "terra"


def test_rejected_only_report_requires_review() -> None:
    field = ExtractedField(
        field_id="f0",
        original_name="ID",
        canonical_name="id",
        value="candidate",
        confidence=0,
        status="needs_review",
        reasons=["missing_evidence"],
        validation_checks=[],
        evidence=[
            FieldEvidence(
                page=1,
                region_id="text-0",
                box=Box(xmin=0, ymin=0, xmax=1, ymax=1),
                route="luna",
                candidate="candidate",
                confidence=0,
            )
        ],
    )
    report = build_confidence_report([], Mock(fields=[]), rejected_fields=[field])
    assert report.review_required
    assert report.review_field_count == 1
