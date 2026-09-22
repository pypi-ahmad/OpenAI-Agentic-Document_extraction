from ade_app.inputs import DocumentInput
from ade_app.models import Box
from ade_app.raster import crop_page, page_count, render_pages


def test_image_render_and_crop(png_bytes: bytes) -> None:
    source = DocumentInput("scan.png", png_bytes)
    page = render_pages(source, (1,))[0]
    assert page_count(source) == 1
    assert page.width == 200
    assert crop_page(page, Box(xmin=0, ymin=0, xmax=0.5, ymax=1)).startswith(b"\x89PNG")
