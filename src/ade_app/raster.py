"""In-memory PDF and image rasterization."""

from __future__ import annotations

import io
import math
from collections.abc import Iterator
from dataclasses import dataclass

import pymupdf
from PIL import Image, ImageOps

from ade_app.constants import DEFAULT_DPI, MAX_IMAGE_PATCHES
from ade_app.inputs import DocumentInput
from ade_app.models import Box, SemanticElement, SemanticFigure, SemanticTable

MAX_RASTER_PIXELS = 20_000_000
MAX_BATCH_RASTER_PIXELS = 100_000_000
IMAGE_FORMATS = {
    ".png": frozenset({"PNG"}),
    ".jpg": frozenset({"JPEG"}),
    ".jpeg": frozenset({"JPEG"}),
    ".webp": frozenset({"WEBP"}),
    ".tif": frozenset({"TIFF"}),
    ".tiff": frozenset({"TIFF"}),
    ".bmp": frozenset({"BMP"}),
}


@dataclass(frozen=True, slots=True)
class RenderedPage:
    source_page: int
    png_bytes: bytes
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class RenderedCrop:
    png_bytes: bytes
    left: int
    top: int
    width: int
    height: int
    page_width: int
    page_height: int


def get_page_count(source: DocumentInput) -> int:
    """Return a validated PDF page count, or one for a supported image."""

    if source.suffix != ".pdf":
        _open_image(source.data, source.suffix)
        return 1
    try:
        with pymupdf.open(stream=source.data, filetype="pdf") as document:
            if document.needs_pass:
                raise ValueError("password-protected PDFs are not supported")
            if document.page_count < 1:
                raise ValueError("PDF contains no pages")
            return document.page_count
    except ValueError:
        raise
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise ValueError("PDF could not be decoded") from error


def rasterize_document(
    source: DocumentInput, pages: tuple[int, ...], dpi: int = DEFAULT_DPI
) -> Iterator[RenderedPage]:
    """Yield selected source pages as bounded, PNG-encoded model inputs."""

    if dpi < 72 or dpi > 600:
        raise ValueError("dpi must be between 72 and 600")
    if source.suffix == ".pdf":
        yield from _rasterize_pdf(source, pages, dpi)
        return
    if pages != (1,):
        raise ValueError("image inputs contain exactly one page")
    image = _open_image(source.data, source.suffix)
    yield _encode_page(image, 1)


def estimate_render_pixels(
    source: DocumentInput, pages: tuple[int, ...], dpi: int = DEFAULT_DPI
) -> int:
    """Estimate bounded raster pixels before retaining selected page images."""

    if dpi < 72 or dpi > 600:
        raise ValueError("dpi must be between 72 and 600")
    if source.suffix != ".pdf":
        if pages != (1,):
            raise ValueError("image inputs contain exactly one page")
        image = _open_image(source.data, source.suffix)
        return image.width * image.height
    try:
        with pymupdf.open(stream=source.data, filetype="pdf") as document:
            if document.needs_pass:
                raise ValueError("password-protected PDFs are not supported")
            total = 0
            for source_page in pages:
                if source_page < 1 or source_page > document.page_count:
                    raise ValueError(f"page {source_page} is outside the PDF")
                rect = document.load_page(source_page - 1).rect
                render_dpi = _bounded_pdf_dpi(rect.width, rect.height, dpi)
                width = max(1, math.ceil(rect.width * render_dpi / 72))
                height = max(1, math.ceil(rect.height * render_dpi / 72))
                total += width * height
            return total
    except ValueError:
        raise
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise ValueError("PDF could not be decoded") from error


def _rasterize_pdf(
    source: DocumentInput, pages: tuple[int, ...], dpi: int
) -> Iterator[RenderedPage]:
    try:
        with pymupdf.open(stream=source.data, filetype="pdf") as document:
            if document.needs_pass:
                raise ValueError("password-protected PDFs are not supported")
            for source_page in pages:
                if source_page < 1 or source_page > document.page_count:
                    raise ValueError(f"page {source_page} is outside the PDF")
                page = document.load_page(source_page - 1)
                render_dpi = _bounded_pdf_dpi(page.rect.width, page.rect.height, dpi)
                pixmap = page.get_pixmap(dpi=render_dpi, alpha=False)
                image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
                yield _encode_page(image, source_page)
    except ValueError:
        raise
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise ValueError("PDF could not be decoded") from error


def _bounded_pdf_dpi(width_points: float, height_points: float, dpi: int) -> int:
    if not all(math.isfinite(value) and value > 0 for value in (width_points, height_points)):
        raise ValueError("PDF page dimensions are invalid")
    pixels = width_points * dpi / 72 * height_points * dpi / 72
    if pixels <= MAX_RASTER_PIXELS:
        return dpi
    bounded = int(dpi * math.sqrt(MAX_RASTER_PIXELS / pixels))
    if bounded < 72:
        raise ValueError("PDF page dimensions exceed the safe raster limit")
    return bounded


def _open_image(data: bytes, suffix: str) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
    except (Image.DecompressionBombError, OSError, ValueError) as error:
        raise ValueError("image could not be decoded") from error
    with image:
        if image.format not in IMAGE_FORMATS[suffix]:
            raise ValueError("image content does not match its file extension")
        if image.width * image.height > MAX_RASTER_PIXELS:
            raise ValueError("image dimensions exceed the safe raster limit")
        return ImageOps.exif_transpose(image).convert("RGB")


def _encode_page(image: Image.Image, source_page: int) -> RenderedPage:
    prepared = _fit_patch_limit(image)
    output = io.BytesIO()
    prepared.save(output, format="PNG", optimize=True)
    return RenderedPage(
        source_page=source_page,
        png_bytes=output.getvalue(),
        width=prepared.width,
        height=prepared.height,
    )


def _fit_patch_limit(image: Image.Image) -> Image.Image:
    result = image
    while _patch_count(result.width, result.height) > MAX_IMAGE_PATCHES:
        scale = math.sqrt(MAX_IMAGE_PATCHES / _patch_count(result.width, result.height)) * 0.995
        size = (max(1, int(result.width * scale)), max(1, int(result.height * scale)))
        result = result.resize(size, Image.Resampling.LANCZOS)
    return result


def _patch_count(width: int, height: int) -> int:
    return math.ceil(width / 32) * math.ceil(height / 32)


def crop_segment(page: RenderedPage, box: Box) -> RenderedCrop:
    """Crop a segment with bounded context from the exact rendered page."""

    segment_width = max(1, round((box.xmax - box.xmin) * page.width))
    segment_height = max(1, round((box.ymax - box.ymin) * page.height))
    margin_x = min(round(page.width * 0.05), max(32, round(segment_width * 0.10)))
    margin_y = min(round(page.height * 0.05), max(32, round(segment_height * 0.10)))
    left = max(0, math.floor(box.xmin * page.width) - margin_x)
    top = max(0, math.floor(box.ymin * page.height) - margin_y)
    right = min(page.width, math.ceil(box.xmax * page.width) + margin_x)
    bottom = min(page.height, math.ceil(box.ymax * page.height) + margin_y)
    if right <= left or bottom <= top:
        raise ValueError("segment has no crop area")
    with Image.open(io.BytesIO(page.png_bytes)) as image:
        output = io.BytesIO()
        image.crop((left, top, right, bottom)).save(output, format="PNG", optimize=True)
    return RenderedCrop(
        output.getvalue(), left, top, right - left, bottom - top, page.width, page.height
    )


def transform_semantic_element_from_crop(
    element: SemanticElement, crop: RenderedCrop
) -> SemanticElement:
    """Transform every crop-relative semantic box into page-relative coordinates."""

    transformed = element.model_copy(deep=True)
    boxes = [transformed.box]
    if isinstance(transformed, SemanticTable):
        for cell in transformed.children:
            boxes.append(cell.box)
    else:
        if isinstance(transformed, SemanticFigure):
            boxes.append(transformed.description.box)
        boxes.extend(line.box for line in transformed.lines)
    seen: set[int] = set()
    for box in boxes:
        if id(box) in seen:
            continue
        seen.add(id(box))
        updated = Box(
            xmin=round((crop.left + box.xmin * crop.width) / crop.page_width, 5),
            ymin=round((crop.top + box.ymin * crop.height) / crop.page_height, 5),
            xmax=round((crop.left + box.xmax * crop.width) / crop.page_width, 5),
            ymax=round((crop.top + box.ymax * crop.height) / crop.page_height, 5),
        )
        box.xmin, box.ymin, box.xmax, box.ymax = (
            updated.xmin,
            updated.ymin,
            updated.xmax,
            updated.ymax,
        )
    return transformed
