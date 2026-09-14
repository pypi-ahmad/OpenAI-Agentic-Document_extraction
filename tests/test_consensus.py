from __future__ import annotations

import io
from typing import Literal

from PIL import Image, ImageDraw

from ade_app.consensus import (
    PeerSegment,
    apply_resolutions,
    build_peer_evidence,
    compare_elements,
)
from ade_app.models import (
    Box,
    FieldResolution,
    SemanticLeaf,
    SemanticLine,
    SemanticLineValue,
    SemanticText,
)
from ade_app.raster import RenderedPage


def _leaf(
    text: str, *, source_kind: Literal["printed", "handwritten", "uncertain"] = "printed"
) -> SemanticLeaf:
    box = Box(xmin=0.1, ymin=0.1, xmax=0.9, ymax=0.3)
    return SemanticLeaf(
        type="text",
        lines=[
            SemanticLine(
                content=[SemanticText(text=text)],
                box=box,
                source_kind=source_kind,
            )
        ],
        box=box,
    )


def _page(page: int, *, detailed: bool) -> RenderedPage:
    image = Image.new("RGB", (200, 100), "white")
    if detailed:
        draw = ImageDraw.Draw(image)
        for x in range(20, 180, 4):
            draw.line((x, 10, x, 35), fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return RenderedPage(page, output.getvalue(), 200, 100)


def test_comparison_ignores_geometry_but_reports_content_disagreement() -> None:
    primary = _leaf("Member ID: 123")
    independent = _leaf("Member ID: 128")
    independent.lines[0].box = Box(xmin=0.2, ymin=0.1, xmax=0.8, ymax=0.3)

    comparison = compare_elements(primary, independent)

    assert comparison.structural_conflicts == ()
    assert [item.field_id for item in comparison.disagreements] == ["line-0"]


def test_resolution_changes_only_semantics_and_preserves_primary_box() -> None:
    primary = _leaf("Member ID: 123")
    independent = _leaf("Member ID: 128")
    comparison = compare_elements(primary, independent)
    original_box = primary.lines[0].box
    resolution = FieldResolution(
        field_id="line-0",
        kind="line",
        status="resolved",
        line=SemanticLineValue(content=[SemanticText(text="Member ID: 128")]),
    )

    resolved, unresolved = apply_resolutions(primary, comparison.disagreements, [resolution])

    assert unresolved == ()
    assert isinstance(resolved, SemanticLeaf)
    assert isinstance(resolved.lines[0].content[0], SemanticText)
    assert resolved.lines[0].content[0].text == "Member ID: 128"
    assert resolved.lines[0].box == original_box


def test_peer_evidence_requires_a_clearer_different_page_stable_region() -> None:
    target = PeerSegment("p1-s0", _page(1, detailed=False), _leaf("Stable header"))
    peer = PeerSegment("p2-s0", _page(2, detailed=True), _leaf("Stable header"))

    evidence = build_peer_evidence([target, peer])

    assert evidence["p1-s0"].source_page == 2
    assert evidence["p1-s0"].peer_clarity > evidence["p1-s0"].target_clarity


def test_handwritten_content_is_not_used_to_cluster_peers() -> None:
    segments = [
        PeerSegment("p1-s0", _page(1, detailed=False), _leaf("John", source_kind="handwritten")),
        PeerSegment("p2-s0", _page(2, detailed=True), _leaf("John", source_kind="handwritten")),
    ]

    assert build_peer_evidence(segments) == {}
