from ade_app.layout import (
    LayoutAnalysis,
    LayoutIssue,
    LayoutRegion,
    RegionCrop,
    _table_markdown,
    decide_routes,
)
from ade_app.models import Box


def test_layout_downsampling_preserves_original_normalized_coordinates() -> None:
    import io

    from PIL import Image

    from ade_app.layout import _bounded_layout_page
    from ade_app.preprocessing import PageTransform, PreparedPage
    from ade_app.raster import RenderedPage

    output = io.BytesIO()
    Image.new("RGB", (2400, 3200), "white").save(output, "PNG")
    page = RenderedPage(1, output.getvalue(), 2400, 3200)
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    prepared = PreparedPage(
        page, page, PageTransform(forward=identity, inverse=identity, operations=[])
    )
    bounded = _bounded_layout_page(prepared)
    assert (bounded.page.width, bounded.page.height) == (1200, 1600)
    box = Box(xmin=0.1, ymin=0.2, xmax=0.8, ymax=0.9)
    assert bounded.box_to_original(box) == prepared.box_to_original(box)
    assert bounded.original is page
    assert prepared.page.width == 2400


def test_parsed_block_confidence_requires_unique_matching_detector_box() -> None:
    from ade_app.layout import _layout_confidence

    block = {"block_label": "text", "block_bbox": [10, 20, 100, 200]}
    box = {"label": "text", "coordinate": [10.4, 20.2, 100.8, 200.1], "score": 0.98}
    payload = {"layout_det_res": {"boxes": [box]}}
    assert _layout_confidence(payload, block) == 0.98
    assert _layout_confidence({}, block) == 0.0
    payload["layout_det_res"]["boxes"].append(box)
    assert _layout_confidence(payload, block) == 0.0


def test_windows_cpu_avoids_unsupported_onednn_backend(monkeypatch) -> None:
    from unittest.mock import Mock

    from ade_app import layout

    monkeypatch.setattr(layout.sys, "platform", "win32")
    factory = Mock()
    layout._create_model(factory, "cpu")
    assert factory.call_args.kwargs["enable_mkldnn"] is False
    factory.reset_mock()
    layout._create_model(factory, "gpu:0")
    assert "enable_mkldnn" not in factory.call_args.kwargs


def test_uncalibrated_local_route_fails_closed_to_terra() -> None:
    analysis = LayoutAnalysis(
        source_page=1,
        regions=[
            LayoutRegion(
                region_id="p1-r0",
                kind="text",
                confidence=0.99,
                box=Box(xmin=0, ymin=0, xmax=1, ymax=1),
                route="local_text",
                text="hello",
            )
        ],
        proposals=[],
    )

    decision = decide_routes(analysis, set())

    assert decision.use_full_page_model
    assert decision.reason == "uncalibrated_routes:local_text"


def test_calibrated_local_route_can_avoid_full_page_terra() -> None:
    analysis = LayoutAnalysis(
        source_page=1,
        regions=[
            LayoutRegion(
                region_id="p1-r0",
                kind="text",
                confidence=0.99,
                box=Box(xmin=0, ymin=0, xmax=1, ymax=1),
                route="local_text",
                text="hello",
            )
        ],
        proposals=[],
    )

    assert not decide_routes(analysis, {"local_text"}).use_full_page_model


def test_partial_layout_fails_closed_to_model_cascade() -> None:
    issue = LayoutIssue(
        code="model_unavailable",
        stage="model_init",
        message="PP-StructureV3 model files are unavailable",
        cause_type="FileNotFoundError",
        attempted_devices=("cpu",),
    )
    analysis = LayoutAnalysis(
        source_page=1,
        regions=[],
        proposals=[],
        status="unavailable",
        attempted_devices=("cpu",),
        issues=[issue],
    )

    decision = decide_routes(analysis, {"local_text", "local_table"})

    assert decision.use_full_page_model
    assert decision.reason == "layout_unavailable:model_unavailable"


def test_crop_pixels_remain_in_memory_but_are_excluded_from_json() -> None:
    crop = RegionCrop(
        png_bytes=b"png",
        box=Box(xmin=0, ymin=0, xmax=1, ymax=1),
        width=10,
        height=10,
    )
    region = LayoutRegion(
        region_id="p1-r0",
        kind="handwritten_text",
        confidence=0.8,
        box=Box(xmin=0, ymin=0, xmax=1, ymax=1),
        route="verification",
        crop=crop,
    )

    assert region.crop is not None
    assert region.crop.png_bytes == b"png"
    assert "png_bytes" not in region.model_dump_json()


def test_simple_high_confidence_table_gets_markdown() -> None:
    html = "<table><tr><th>Name</th><th>ID</th></tr><tr><td>A</td><td>1</td></tr></table>"

    simple, markdown = _table_markdown(html, 0.85)

    assert simple is True
    assert markdown == "| Name | ID |\n| --- | --- |\n| A | 1 |"


def test_spanning_or_low_confidence_table_keeps_html_only() -> None:
    spanning = "<table><tr><td colspan='2'>Name</td></tr></table>"

    assert _table_markdown(spanning, 0.99) == (False, None)
    assert _table_markdown("<table><tr><td>Name</td></tr></table>", 0.84) == (
        True,
        None,
    )
