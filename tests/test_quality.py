from __future__ import annotations

from ade_app.models import (
    AuditFinding,
    Box,
    SegmentAudit,
    SemanticPageExtraction,
    SemanticTable,
    SemanticTableCell,
)
from ade_app.quality import (
    CalibrationSample,
    fit_quality_profile,
    measure_segment,
    segment_features,
)
from ade_app.rendering import render_semantic_page


def test_clean_supported_segment_scores_from_profile(sample_page, quality_profile) -> None:
    audit = SegmentAudit(
        segment_index=0,
        completeness="complete",
        image_agreement="supported",
        findings=[],
    )
    result = measure_segment(sample_page, 0, audit, quality_profile)

    assert result.features == (1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
    assert result.score >= 90
    assert result.reasons == ()


def test_routing_features_do_not_run_business_validation(sample_page, monkeypatch) -> None:
    monkeypatch.setattr(
        "ade_app.quality.verify_sensitive_values",
        lambda text: (_ for _ in ()).throw(AssertionError("business validation ran")),
    )
    audit = SegmentAudit(
        segment_index=0,
        completeness="complete",
        image_agreement="supported",
        findings=[],
    )

    features, reasons = segment_features(sample_page, 0, audit)

    assert features[-1] == 1.0
    assert not any(reason.startswith("verifier:") for reason in reasons)


def test_audit_findings_lower_quality_features(sample_page, quality_profile) -> None:
    audit = SegmentAudit(
        segment_index=0,
        completeness="uncertain",
        image_agreement="contradicted",
        findings=[AuditFinding(code="suspected_mistranscription", severity="high")],
    )
    result = measure_segment(sample_page, 0, audit, quality_profile)

    assert result.features[1] == 0.5
    assert result.features[5] == 0.0
    assert result.features[6] < 1.0
    assert "high:suspected_mistranscription" in result.reasons


def test_calibration_uses_leave_one_document_out() -> None:
    samples = [
        CalibrationSample(f"doc-{index % 3}", (float(index % 2),) * 7, quality)
        for index, quality in enumerate([99.0, 20.0, 98.0, 10.0, 97.0, 30.0] * 3)
    ]
    profile = fit_quality_profile(samples, prompt_hashes={}, groundtruth_hashes={})

    assert profile.validation["document_count"] == 3
    assert profile.validation["sample_count"] == len(samples)
    assert len(profile.coefficients) == 7


def test_calibration_fails_closed_when_no_safe_positive_coverage_exists() -> None:
    samples = [CalibrationSample(document, (1.0,) * 7, 20.0) for document in ("doc-a", "doc-b")]

    profile = fit_quality_profile(samples, prompt_hashes={}, groundtruth_hashes={})

    assert profile.profile_version == 4
    assert profile.model_id == "gpt-5.6-terra"
    assert profile.reasoning_effort == "medium"
    assert profile.raw_decision_boundary == 100.0
    assert profile.validation["accepted_count"] == 0
    assert profile.score((1.0,) * 7) < 90.0


def test_overlapping_table_is_routed_as_inconsistent_instead_of_rejecting_page() -> None:
    box = Box(xmin=0.1, ymin=0.1, xmax=0.9, ymax=0.9)
    table = SemanticTable(
        children=[
            SemanticTableCell(row=0, col=0, lines=[], box=box),
            SemanticTableCell(row=0, col=0, lines=[], box=box),
        ],
        box=box,
    )
    page = render_semantic_page(SemanticPageExtraction(children=[table]))
    audit = SegmentAudit(
        segment_index=0,
        completeness="uncertain",
        image_agreement="uncertain",
        findings=[],
    )

    features, reasons = segment_features(page, 0, audit)

    assert features[4] == 0.0
    assert "table_inconsistency" in reasons
