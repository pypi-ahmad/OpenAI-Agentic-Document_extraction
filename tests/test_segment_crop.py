from __future__ import annotations

import io

from PIL import Image

from ade_app.models import Box, SemanticLeaf, SemanticLine, SemanticText
from ade_app.raster import RenderedPage, crop_segment, transform_semantic_element_from_crop


def _page() -> RenderedPage:
    output = io.BytesIO()
    Image.new("RGB", (1000, 800), "white").save(output, format="PNG")
    return RenderedPage(1, output.getvalue(), 1000, 800)


def test_crop_adds_context_and_clips_to_page() -> None:
    crop = crop_segment(_page(), Box(xmin=0.0, ymin=0.0, xmax=0.1, ymax=0.1))

    assert crop.left == 0
    assert crop.top == 0
    assert crop.width > 100
    assert crop.height > 80


def test_crop_coordinates_transform_back_to_page() -> None:
    page = _page()
    crop = crop_segment(page, Box(xmin=0.2, ymin=0.2, xmax=0.4, ymax=0.4))
    box = Box(xmin=0.0, ymin=0.0, xmax=1.0, ymax=1.0)
    element = SemanticLeaf(
        type="text",
        lines=[SemanticLine(content=[SemanticText(text="text")], box=box)],
        box=box,
    )

    transformed = transform_semantic_element_from_crop(element, crop)

    assert isinstance(transformed, SemanticLeaf)
    assert transformed.box.xmin == crop.left / page.width
    assert transformed.box.ymax == (crop.top + crop.height) / page.height
    assert transformed.lines[0].box == transformed.box
