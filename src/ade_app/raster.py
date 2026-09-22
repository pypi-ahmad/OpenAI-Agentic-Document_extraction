"""Safe PDF/image rendering and model-requested crop handling."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import pymupdf
from PIL import Image, ImageOps

from ade_app.inputs import DocumentInput
from ade_app.models import Box

MAX_PIXELS = 20_000_000


@dataclass(frozen=True, slots=True)
class RenderedPage:
    page: int
    png: bytes
    width: int
    height: int


def page_count(source: DocumentInput) -> int:
    if source.suffix != ".pdf":
        _open_image(source)
        return 1
    try:
        with pymupdf.open(stream=source.data, filetype="pdf") as document:
            if document.needs_pass or document.page_count < 1:
                raise ValueError("PDF is encrypted or empty")
            return document.page_count
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise ValueError("PDF could not be decoded") from error


def render_pages(
    source: DocumentInput, pages: tuple[int, ...], dpi: int = 300
) -> list[RenderedPage]:
    if source.suffix != ".pdf":
        if pages != (1,):
            raise ValueError("image inputs contain one page")
        return [_encode(_open_image(source), 1)]
    rendered: list[RenderedPage] = []
    try:
        with pymupdf.open(stream=source.data, filetype="pdf") as document:
            if document.needs_pass:
                raise ValueError("password-protected PDFs are not supported")
            for number in pages:
                if number < 1 or number > document.page_count:
                    raise ValueError(f"page {number} is outside the PDF")
                page = document[number - 1]
                scale = dpi / 72
                pixels = page.rect.width * scale * page.rect.height * scale
                if pixels > MAX_PIXELS:
                    scale *= math.sqrt(MAX_PIXELS / pixels)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                rendered.append(_encode(Image.open(io.BytesIO(pixmap.tobytes("png"))), number))
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise ValueError("PDF could not be decoded") from error
    return rendered


def crop_page(page: RenderedPage, box: Box, rotate: int = 0) -> bytes:
    with Image.open(io.BytesIO(page.png)) as image:
        cropped = image.crop(
            (
                round(box.xmin * image.width),
                round(box.ymin * image.height),
                round(box.xmax * image.width),
                round(box.ymax * image.height),
            )
        )
        if rotate:
            cropped = cropped.rotate(-rotate, expand=True)
        return _png(cropped)


def _open_image(source: DocumentInput) -> Image.Image:
    try:
        with Image.open(io.BytesIO(source.data)) as image:
            image.load()
            if image.width * image.height > MAX_PIXELS:
                raise ValueError("image dimensions exceed the safe limit")
            return ImageOps.exif_transpose(image).convert("RGB")
    except (Image.DecompressionBombError, OSError) as error:
        raise ValueError("image could not be decoded") from error


def _encode(image: Image.Image, page: int) -> RenderedPage:
    image = image.convert("RGB")
    return RenderedPage(page, _png(image), image.width, image.height)


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
