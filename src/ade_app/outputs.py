"""Annotated PDF rendering and download bundle generation.

Responsible for rendering visual bounding-box annotated review PDFs (with element badges,
status coloring, and right-hand metadata sidebars) and packaging artifacts into clean ZIP archives.
Must NOT execute extraction, alter bounding-box coordinates, or write unrequested files to disk.
Next: ade_app.contracts where bundle properties are exposed, or streamlit_app.py for UI downloads.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import pymupdf

from ade_app.models import ExtractionDocumentV2, ExtractionDocumentV3, GroundTruthDocument
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
    confidence_filename: str
    confidence_text: str
    annotated_pdf_filename: str
    annotated_pdf: bytes
    manifest: dict[str, Any]


def build_annotated_pdf(
    pages: tuple[RenderedPage, ...],
    artifact: ExtractionDocumentV2 | ExtractionDocumentV3 | GroundTruthDocument,
    *,
    untrusted_elements: dict[int, set[str]] | None = None,
) -> tuple[bytes, tuple[AnnotationLimitation, ...]]:
    """Build a numbered, color-coded field review PDF with a right sidebar."""

    page_nodes = {page.grounding.page: page for page in artifact.structure.children}
    untrusted_elements = untrusted_elements or {}
    limitations: list[AnnotationLimitation] = []
    output = pymupdf.open()
    for rendered in pages:
        scale = min(1.0, 792.0 / max(rendered.width, rendered.height))
        image_width, height = rendered.width * scale, rendered.height * scale
        sidebar_width = 220.0
        pdf_page = output.new_page(width=image_width + sidebar_width, height=height)
        page_rect = pymupdf.Rect(0, 0, image_width, height)
        pdf_page.insert_image(page_rect, stream=rendered.png_bytes)
        page_node = page_nodes.get(rendered.source_page)
        if page_node is None or page_node.status == "failed":
            limitations.append(
                AnnotationLimitation(rendered.source_page, None, "page extraction failed")
            )
            continue
        pdf_page.draw_rect(
            pymupdf.Rect(image_width, 0, image_width + sidebar_width, height),
            fill=(0.97, 0.97, 0.97),
            color=None,
            overlay=True,
        )
        pdf_page.insert_text(
            pymupdf.Point(image_width + 10, 18),
            f"Page {rendered.source_page} — field review",
            fontsize=10,
            color=(0.1, 0.1, 0.1),
            overlay=True,
        )
        if not isinstance(artifact, (ExtractionDocumentV2, ExtractionDocumentV3)):
            for element in page_node.children:
                if element.id in untrusted_elements.get(rendered.source_page, set()):
                    limitations.append(
                        AnnotationLimitation(
                            rendered.source_page, element.id, "untrusted segment omitted"
                        )
                    )
                    continue
                box = element.grounding.box
                rect = pymupdf.Rect(
                    box.xmin * image_width,
                    box.ymin * height,
                    box.xmax * image_width,
                    box.ymax * height,
                )
                pdf_page.draw_rect(rect, color=(0.9, 0.55, 0.05), overlay=True)
                pdf_page.insert_text(
                    pymupdf.Point(rect.x0, max(8.0, rect.y0)), element.id, fontsize=8
                )
        entries = []
        seen: set[tuple[str, float, float, float, float]] = set()
        for field in getattr(artifact, "fields", ()):
            for evidence in field.evidence:
                if evidence.page != rendered.source_page:
                    continue
                key = (
                    field.field_id,
                    evidence.box.xmin,
                    evidence.box.ymin,
                    evidence.box.xmax,
                    evidence.box.ymax,
                )
                if key not in seen:
                    seen.add(key)
                    entries.append((field, evidence))
        for number, (field, evidence) in enumerate(entries, 1):
            box = evidence.box
            if box.xmax <= box.xmin or box.ymax <= box.ymin:
                limitations.append(
                    AnnotationLimitation(
                        rendered.source_page, evidence.region_id, "zero-area field box omitted"
                    )
                )
                continue
            rect = pymupdf.Rect(
                box.xmin * image_width,
                box.ymin * height,
                box.xmax * image_width,
                box.ymax * height,
            )
            color = (
                (0.1, 0.6, 0.2)
                if field.status == "accepted"
                and field.confidence is not None
                and field.confidence >= (0.9 if isinstance(artifact, ExtractionDocumentV3) else 90)
                else (0.9, 0.55, 0.05)
                if field.status == "accepted"
                else (0.85, 0.1, 0.1)
            )
            pdf_page.draw_rect(rect, color=color, width=1.5, overlay=True)
            pdf_page.insert_text(
                pymupdf.Point(rect.x0, max(8.0, rect.y0 - 2.0)),
                str(number),
                fontsize=8,
                color=color,
                overlay=True,
            )
            problems = (
                "; ".join(check.message for check in field.validation_checks if not check.passed)
                or "None"
            )
            sidebar = (
                f"{number}. {field.original_name}\nValue: {field.value}\n"
                f"Confidence: {_confidence_label(field.confidence, artifact)}  {field.status}\n"
                f"Issues: {problems}"
            )
            top = 28 + (number - 1) * 64
            if top + 60 > height:
                continue
            pdf_page.insert_textbox(
                pymupdf.Rect(image_width + 10, top, image_width + sidebar_width - 8, top + 58),
                sidebar,
                fontsize=7.5,
                color=color,
                overlay=True,
            )
        sidebar_capacity = max(1, int((height - 28) // 64))
        overflow = entries[sidebar_capacity:]
        for start in range(0, len(overflow), 10):
            continuation = output.new_page(width=612, height=792)
            continuation.insert_text(
                pymupdf.Point(24, 30),
                f"Page {rendered.source_page} — field review continued",
                fontsize=12,
            )
            for row, (field, _evidence) in enumerate(overflow[start : start + 10]):
                number = sidebar_capacity + start + row + 1
                problems = (
                    "; ".join(
                        check.message for check in field.validation_checks if not check.passed
                    )
                    or "None"
                )
                text = (
                    f"{number}. {field.original_name}\nValue: {field.value}\n"
                    f"Confidence: {_confidence_label(field.confidence, artifact)}  {field.status}\n"
                    f"Issues: {problems}"
                )
                continuation.insert_textbox(
                    pymupdf.Rect(24, 48 + row * 70, 588, 108 + row * 70),
                    text,
                    fontsize=9,
                )
    data = output.tobytes(deflate=True)
    output.close()
    return data, tuple(limitations)


def build_annotation_failure_pdf() -> bytes:
    """Return a valid review PDF when normal annotation cannot be completed."""

    output = pymupdf.open()
    page = output.new_page(width=612, height=792)
    page.insert_textbox(
        pymupdf.Rect(48, 72, 564, 180),
        "Annotated review could not be generated.\n"
        "Use the confidence report and source document for review.",
        fontsize=12,
        color=(0.75, 0.1, 0.1),
    )
    data = output.tobytes(deflate=True)
    output.close()
    return data


def build_output_bundle(
    *,
    markdown_filename: str,
    markdown: str,
    json_filename: str,
    json_text: str,
    confidence_filename: str,
    confidence_text: str,
    annotated_pdf_filename: str,
    annotated_pdf: bytes,
    manifest: dict[str, Any],
) -> bytes:
    """Package all run artifacts without touching the filesystem."""

    for filename in (
        markdown_filename,
        json_filename,
        confidence_filename,
        annotated_pdf_filename,
    ):
        _validate_archive_component(filename)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(markdown_filename, markdown)
        archive.writestr(json_filename, json_text)
        archive.writestr(confidence_filename, confidence_text)
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
            entry.confidence_filename,
            entry.annotated_pdf_filename,
        ):
            _validate_archive_component(filename)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            prefix = f"{entry.folder}/"
            archive.writestr(prefix + entry.markdown_filename, entry.markdown)
            archive.writestr(prefix + entry.json_filename, entry.json_text)
            archive.writestr(prefix + entry.confidence_filename, entry.confidence_text)
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


def _confidence_label(value: float | None, artifact: object) -> str:
    if value is None:
        return "uncalibrated"
    return f"{value * (100 if isinstance(artifact, ExtractionDocumentV3) else 1):.1f}%"
