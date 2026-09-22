import pytest
from pydantic import ValidationError

from ade_app.layout import LayoutAnalysis, LayoutRegion
from ade_app.models import Box, SegmentAudit, SemanticCheckbox
from ade_app.openai_client import (
    _contains_critical_field,
    _evidence_score,
    _region_inputs,
)


def _region(confidence: float, *, kind: str = "text") -> LayoutRegion:
    box = Box(xmin=0.1, ymin=0.1, xmax=0.9, ymax=0.2)
    return LayoutRegion(
        region_id="p1-r0",
        kind=kind,
        confidence=confidence,
        box=box,
        prepared_box=box,
        route="local_text" if kind == "text" else "verification",
        text="Name: Jane Doe" if kind == "text" else None,
    )


def test_printed_routing_uses_90_percent_boundary() -> None:
    high = LayoutAnalysis(source_page=1, regions=[_region(0.90)], proposals=[])
    low = LayoutAnalysis(source_page=1, regions=[_region(0.899)], proposals=[])

    assert _region_inputs(high)[0].route == "primary"
    assert _region_inputs(low)[0].route == "verification"


def test_uncertain_audit_caps_confidence_below_sol_threshold() -> None:
    audit = SegmentAudit(
        segment_index=0,
        completeness="uncertain",
        image_agreement="supported",
        findings=[],
    )

    assert _evidence_score(0.99, audit) == (74.0, ["uncertain_model_audit"])


def test_critical_aliases_are_detected() -> None:
    assert _contains_critical_field("Member ID: ABC123")
    assert _contains_critical_field("Date of Birth: 01/02/2000")
    assert not _contains_critical_field("Fax: 555-0100")


def test_checkbox_state_is_strict_boolean() -> None:
    with pytest.raises(ValidationError):
        SemanticCheckbox.model_validate({"checked": "checked"})
