from __future__ import annotations

import io

import pytest
from PIL import Image

from ade_app.inputs import DocumentInput
from ade_app.raster import MAX_RASTER_PIXELS, _bounded_pdf_dpi, get_page_count, rasterize_document


def test_image_rasterization_produces_rgb_png() -> None:
    source_bytes = io.BytesIO()
    Image.new("L", (64, 48), color=255).save(source_bytes, format="TIFF")
    source = DocumentInput(filename="sample.tiff", data=source_bytes.getvalue())

    assert get_page_count(source) == 1
    pages = list(rasterize_document(source, (1,)))
    assert len(pages) == 1
    assert pages[0].png_bytes.startswith(b"\x89PNG")
    assert (pages[0].width, pages[0].height) == (64, 48)


def test_pdf_raster_dpi_is_bounded_before_pixmap_allocation() -> None:
    dpi = _bounded_pdf_dpi(2_000, 2_000, 300)

    assert dpi < 300
    assert 2_000 * dpi / 72 * 2_000 * dpi / 72 <= MAX_RASTER_PIXELS
    huge_dpi = _bounded_pdf_dpi(10_000, 10_000, 300)
    assert huge_dpi < 72
    assert 10_000 * huge_dpi / 72 * 10_000 * huge_dpi / 72 <= MAX_RASTER_PIXELS
    with pytest.raises(ValueError, match="dimensions are invalid"):
        _bounded_pdf_dpi(float("inf"), 100, 300)


def test_image_content_must_match_declared_extension() -> None:
    source_bytes = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(source_bytes, format="JPEG")
    source = DocumentInput(filename="misnamed.png", data=source_bytes.getvalue())

    with pytest.raises(ValueError, match="does not match"):
        get_page_count(source)


def test_malformed_pdf_has_safe_decode_error() -> None:
    source = DocumentInput(filename="damaged.pdf", data=b"%PDF-1.7\nnot-a-real-pdf")

    with pytest.raises(ValueError, match=r"^PDF could not be decoded$"):
        get_page_count(source)

    with pytest.raises(ValueError, match=r"^PDF could not be decoded$"):
        list(rasterize_document(source, (1,)))
