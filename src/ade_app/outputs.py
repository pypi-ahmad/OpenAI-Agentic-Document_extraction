"""Annotated PDF and download bundle generation."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import pymupdf

from ade_app.models import GroundTruthDocument, TableElement
from ade_app.raster import RenderedPage


@dataclass(frozen=True, slots=True)
class AnnotationLimitation:
    source_page: int
    element_id: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class BatchBundleEntry:
    folder: str
    markdown_filename: str
    markdown: str
    json_filename: str
    json_text: str
    annotated_pdf_filename: str
    annotated_pdf: bytes
    manifest: dict[str, Any]


def build_annotated_pdf(
    pages: tuple[RenderedPage, ...],
    artifact: GroundTruthDocument,
    *,
    untrusted_elements: dict[int, set[str]] | None = None,
) -> tuple[bytes, tuple[AnnotationLimitation, ...]]:
    """Overlay trustworthy normalized boxes on the exact images sent to the model."""

    page_nodes = {page.grounding.page: page for page in artifact.structure.children}
    untrusted_elements = untrusted_elements or {}
    limitations: list[AnnotationLimitation] = []
    output = pymupdf.open()
    for rendered in pages:
        scale = min(1.0, 792.0 / max(rendered.width, rendered.height))
        width, height = rendered.width * scale, rendered.height * scale
        pdf_page = output.new_page(width=width, height=height)
        page_rect = pdf_page.rect
        pdf_page.insert_image(page_rect, stream=rendered.png_bytes)
        page_node = page_nodes.get(rendered.source_page)
        if page_node is None or page_node.status == "failed":
            limitations.append(
                AnnotationLimitation(rendered.source_page, None, "page extraction failed")
            )
            continue
        page_untrusted = untrusted_elements.get(rendered.source_page, set())
        elements = []
        for element in page_node.children:
            if element.id in page_untrusted:
                limitations.append(
                    AnnotationLimitation(
                        rendered.source_page,
                        element.id,
                        "uncertain segment; overlay omitted pending human review",
                    )
                )
                continue
            elements.append(element)
            if isinstance(element, TableElement):
                elements.extend(element.children)
        for element in elements:
            box = element.grounding.box
            if box.xmax <= box.xmin or box.ymax <= box.ymin:
                limitations.append(
                    AnnotationLimitation(rendered.source_page, element.id, "zero-area box omitted")
                )
                continue
            rect = pymupdf.Rect(
                box.xmin * width,
                box.ymin * height,
                box.xmax * width,
                box.ymax * height,
            )
            pdf_page.draw_rect(rect, color=(1, 0, 0), width=1.2, overlay=True)
            label_y = max(8.0, rect.y0 - 2.0)
            pdf_page.insert_text(
                pymupdf.Point(rect.x0, label_y),
                f"{element.id} [{element.type}]",
                fontsize=7,
                color=(0.8, 0, 0),
                overlay=True,
            )
    data = output.tobytes(deflate=True)
    output.close()
    return data, tuple(limitations)


def build_output_bundle(
    *,
    markdown_filename: str,
    markdown: str,
    json_filename: str,
    json_text: str,
    annotated_pdf_filename: str,
    annotated_pdf: bytes,
    manifest: dict[str, Any],
) -> bytes:
    """Package all run artifacts without touching the filesystem."""

    for filename in (markdown_filename, json_filename, annotated_pdf_filename):
        _validate_archive_component(filename)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(markdown_filename, markdown)
        archive.writestr(json_filename, json_text)
        archive.writestr(annotated_pdf_filename, annotated_pdf)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    return output.getvalue()


def build_batch_output_bundle(
    *, entries: list[BatchBundleEntry], manifest: dict[str, Any]
) -> bytes:
    """Package successful document outputs under deterministic batch folders."""

    folders = [entry.folder for entry in entries]
    if len(folders) != len(set(folders)):
        raise ValueError("batch output folders must be unique")
    for entry in entries:
        _validate_archive_component(entry.folder)
        for filename in (
            entry.markdown_filename,
            entry.json_filename,
            entry.annotated_pdf_filename,
        ):
            _validate_archive_component(filename)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            prefix = f"{entry.folder}/"
            archive.writestr(prefix + entry.markdown_filename, entry.markdown)
            archive.writestr(prefix + entry.json_filename, entry.json_text)
            archive.writestr(prefix + entry.annotated_pdf_filename, entry.annotated_pdf)
            archive.writestr(prefix + "manifest.json", json.dumps(entry.manifest, indent=2))
        archive.writestr("batch-manifest.json", json.dumps(manifest, indent=2))
    return output.getvalue()


def _validate_archive_component(value: str) -> None:
    """Reject archive names that could escape or confuse an extraction directory."""

    if (
        not value
        or value in {".", ".."}
        or PurePosixPath(value).name != value
        or any(character in '<>:"/\\|?*' for character in value)
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("archive names must be safe portable path components")
