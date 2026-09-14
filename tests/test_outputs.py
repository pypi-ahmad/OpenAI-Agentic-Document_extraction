from __future__ import annotations

import io
import json
import zipfile
from decimal import Decimal

import pymupdf
import pytest
from PIL import Image

from ade_app.outputs import build_annotated_pdf, build_output_bundle
from ade_app.raster import RenderedPage
from ade_app.rendering import PageOutcome, render_document

JOB_ID = "parse-01m1be6gqfkfrk72qg4jf93fqz"


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (100, 80), "white").save(output, "PNG")
    return output.getvalue()


def test_annotated_pdf_uses_selected_page_order(sample_page) -> None:
    artifact = render_document(
        page_count=4,
        outcomes=[
            PageOutcome(source_page=1, failure_reason="failed"),
            PageOutcome(source_page=3, extraction=sample_page),
        ],
        duration_ms=1,
        cost_usd=Decimal(0),
        job_id=JOB_ID,
    )
    pdf, limitations = build_annotated_pdf(
        (
            RenderedPage(1, _png(), 100, 80),
            RenderedPage(3, _png(), 100, 80),
        ),
        artifact,
    )
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        assert document.page_count == 2
    assert limitations[0].source_page == 1
    assert limitations[0].reason == "page extraction failed"


def test_annotated_pdf_omits_only_untrusted_segment(sample_page) -> None:
    artifact = render_document(
        page_count=2,
        outcomes=[
            PageOutcome(source_page=1, extraction=sample_page),
            PageOutcome(source_page=2, extraction=sample_page),
        ],
        duration_ms=1,
        cost_usd=Decimal(0),
        job_id=JOB_ID,
    )
    first_id = artifact.structure.children[0].children[0].id

    pdf, limitations = build_annotated_pdf(
        (
            RenderedPage(1, _png(), 100, 80),
            RenderedPage(2, _png(), 100, 80),
        ),
        artifact,
        untrusted_elements={1: {first_id}},
    )

    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        assert first_id not in document[0].get_text()
        assert artifact.structure.children[1].children[0].id in document[1].get_text()
    assert [(item.source_page, item.element_id) for item in limitations] == [(1, first_id)]


def test_output_bundle_contains_all_files() -> None:
    bundle = build_output_bundle(
        confidence_filename="sample.confidence.json",
        confidence_text="{}",
        markdown_filename="sample.parse.md",
        markdown="# Sample",
        json_filename="sample.parse.json",
        json_text='{"ok": true}',
        annotated_pdf_filename="sample.annotated.pdf",
        annotated_pdf=b"%PDF-test",
        manifest={"selected_pages": [1], "estimated_cost_usd": "0.1"},
    )
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert set(archive.namelist()) == {
            "sample.parse.md",
            "sample.parse.json",
            "sample.confidence.json",
            "sample.annotated.pdf",
            "manifest.json",
        }
        assert json.loads(archive.read("manifest.json"))["selected_pages"] == [1]


def test_output_bundle_rejects_unsafe_archive_names() -> None:
    with pytest.raises(ValueError, match="safe portable"):
        build_output_bundle(
            confidence_filename="sample.confidence.json",
            confidence_text="{}",
            markdown_filename="../sample.md",
            markdown="text",
            json_filename="sample.json",
            json_text="{}",
            annotated_pdf_filename="sample.pdf",
            annotated_pdf=b"pdf",
            manifest={},
        )
