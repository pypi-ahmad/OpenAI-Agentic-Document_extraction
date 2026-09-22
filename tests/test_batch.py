from __future__ import annotations

import io
import json
import threading
import time
import zipfile

import pymupdf
from PIL import Image

from ade_app.batch import MAX_BATCH_FILES, MAX_FILE_WORKERS, BatchDocument, extract_documents
from ade_app.cost import TokenUsage
from ade_app.inputs import DocumentInput
from ade_app.openai_client import PageResponse
from ade_app.raster import RenderedPage


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (100, 80), "white").save(output, "PNG")
    return output.getvalue()


def _documents(count: int) -> tuple[BatchDocument, ...]:
    image = _png()
    return tuple(
        BatchDocument(f"item-{index}", DocumentInput(f"file-{index}.png", image), (1,))
        for index in range(count)
    )


class ConcurrentExtractor:
    def __init__(self, sample_page) -> None:
        self.sample_page = sample_page
        self.active = 0
        self.maximum_active = 0
        self.lock = threading.Lock()

    def extract(self, page, *, job_id: str, page_count: int) -> PageResponse:
        del job_id, page_count
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        time.sleep(0.05)
        with self.lock:
            self.active -= 1
        usage = TokenUsage(input_tokens=10, cached_input_tokens=2, output_tokens=3)
        return PageResponse(
            extraction=self.sample_page,
            usage=usage,
            response_id=f"response-{page.source_page}",
            request_id=f"request-{page.source_page}",
            service_tier="standard",
            range_repairs=0,
            usage_by_model=(("gpt-6-sol", usage),),
            models_used=("gpt-6-sol",),
        )


def _render(source, pages, dpi) -> tuple[RenderedPage, ...]:
    del source, dpi
    return tuple(RenderedPage(page, _png(), 100, 80) for page in pages)


def test_files_run_concurrently_and_results_keep_upload_order(sample_page) -> None:
    extractor = ConcurrentExtractor(sample_page)
    callback_threads: list[int] = []
    caller_thread = threading.get_ident()

    result = extract_documents(
        _documents(4),
        extractor,
        render_pages=_render,
        progress=lambda _event: callback_threads.append(threading.get_ident()),
    )

    assert extractor.maximum_active == 4
    assert [item.item_id for item in result.files] == [f"item-{index}" for index in range(4)]
    assert result.usage.input_tokens == 40
    assert callback_threads and set(callback_threads) == {caller_thread}


def test_default_file_concurrency_is_security_bounded(sample_page) -> None:
    extractor = ConcurrentExtractor(sample_page)

    result = extract_documents(_documents(6), extractor, render_pages=_render)

    assert extractor.maximum_active == MAX_FILE_WORKERS == 4
    assert [item.item_id for item in result.files] == [f"item-{index}" for index in range(6)]


def test_pages_within_one_pdf_run_concurrently_in_source_order(sample_page) -> None:
    pdf = pymupdf.open()
    pdf.new_page()
    pdf.new_page()
    source = DocumentInput("two-pages.pdf", pdf.tobytes())
    pdf.close()
    extractor = ConcurrentExtractor(sample_page)

    result = extract_documents(
        (BatchDocument("pdf", source, (1, 2)),), extractor, render_pages=_render
    )

    assert extractor.maximum_active == 2
    run = result.files[0].run
    assert run is not None
    assert [page.source_page for page in run.pages] == [1, 2]


def test_batch_rejects_more_than_twenty_files(sample_page) -> None:
    extractor = ConcurrentExtractor(sample_page)

    try:
        extract_documents(_documents(MAX_BATCH_FILES + 1), extractor, render_pages=_render)
    except ValueError as error:
        assert "at most 20 files" in str(error)
    else:
        raise AssertionError("oversized batch was accepted")


def test_batch_rejects_duplicate_or_unsorted_pages(sample_page) -> None:
    source = DocumentInput("sample.png", _png())

    for pages in ((1, 1), (2, 1)):
        try:
            extract_documents(
                (BatchDocument("sample", source, pages),),
                ConcurrentExtractor(sample_page),
                render_pages=_render,
            )
        except ValueError as error:
            assert "strictly increasing" in str(error)
        else:
            raise AssertionError(f"invalid page selection was accepted: {pages}")


def test_batch_rejects_unsafe_total_upload_size(sample_page, monkeypatch) -> None:
    monkeypatch.setattr("ade_app.batch.MAX_BATCH_BYTES", 10)
    source = DocumentInput("large.png", b"x" * 11)

    try:
        extract_documents(
            (BatchDocument("large", source, (1,)),),
            ConcurrentExtractor(sample_page),
            render_pages=_render,
        )
    except ValueError as error:
        assert "total upload size" in str(error)
    else:
        raise AssertionError("unsafe batch size was accepted")


def test_batch_rejects_excessive_selected_pages(sample_page, monkeypatch) -> None:
    monkeypatch.setattr("ade_app.batch.MAX_BATCH_PAGES", 2)
    source = DocumentInput("sample.png", _png())

    try:
        extract_documents(
            (BatchDocument("sample", source, (1, 2, 3)),),
            ConcurrentExtractor(sample_page),
            render_pages=_render,
        )
    except ValueError as error:
        assert "selected pages" in str(error)
    else:
        raise AssertionError("unsafe page count was accepted")


def test_document_failure_is_isolated_and_batch_zip_is_exact(sample_page) -> None:
    image = _png()
    documents = (
        BatchDocument("first", DocumentInput("same.png", image), (1,)),
        BatchDocument("failed", DocumentInput("bad.png", image), (1,)),
        BatchDocument("second", DocumentInput("same.png", image), (1,)),
    )

    def render(source, pages, dpi) -> tuple[RenderedPage, ...]:
        if source.filename == "bad.png":
            raise ValueError("damaged image")
        return _render(source, pages, dpi)

    result = extract_documents(documents, ConcurrentExtractor(sample_page), render_pages=render)

    assert [item.status for item in result.files] == ["ok", "failed", "ok"]
    assert result.files[1].failure_reason == "ValueError: document processing failed"
    with zipfile.ZipFile(io.BytesIO(result.zip_bytes)) as archive:
        assert set(archive.namelist()) == {
            "01_same/same.parse.md",
            "01_same/same.parse.json",
            "01_same/same.draft.md",
            "01_same/same.draft.json",
            "01_same/same.confidence.json",
            "01_same/same.annotated.pdf",
            "01_same/manifest.json",
            "03_same/same.parse.md",
            "03_same/same.parse.json",
            "03_same/same.draft.md",
            "03_same/same.draft.json",
            "03_same/same.confidence.json",
            "03_same/same.annotated.pdf",
            "03_same/manifest.json",
            "batch-manifest.json",
        }
        manifest = json.loads(archive.read("batch-manifest.json"))
    assert manifest["successful_file_count"] == 2
    assert manifest["partial_file_count"] == 0
    assert manifest["failed_file_count"] == 1
    assert manifest["completed_page_count"] == 2
    assert manifest["failed_page_count"] == 1
    assert manifest["review_required"] is True
    assert manifest["review_required_file_count"] == 1
    assert [item["status"] for item in manifest["files"]] == ["ok", "failed", "ok"]


def test_partial_document_counts_failed_pages_without_claiming_success(sample_page) -> None:
    class PartialExtractor(ConcurrentExtractor):
        def extract(self, page, *, job_id: str, page_count: int) -> PageResponse:
            if page.source_page == 2:
                raise ValueError("invalid page")
            return super().extract(page, job_id=job_id, page_count=page_count)

    pdf = pymupdf.open()
    pdf.new_page()
    pdf.new_page()
    source = DocumentInput("partial.pdf", pdf.tobytes())
    pdf.close()

    result = extract_documents(
        (BatchDocument("partial", source, (1, 2)),),
        PartialExtractor(sample_page),
        render_pages=_render,
    )

    assert result.files[0].status == "partial"
    assert result.manifest["successful_file_count"] == 0
    assert result.manifest["partial_file_count"] == 1
    assert result.manifest["failed_file_count"] == 0
    assert result.manifest["completed_page_count"] == 1
    assert result.manifest["failed_page_count"] == 1
    assert result.manifest["review_required_file_count"] == 1


def test_post_extraction_failure_keeps_billed_usage_and_cost(sample_page, monkeypatch) -> None:
    def fail_annotation(*_args, **_kwargs):
        raise RuntimeError("annotation failed")

    monkeypatch.setattr("ade_app.pipeline.build_annotated_pdf", fail_annotation)
    progress = []
    result = extract_documents(
        _documents(1),
        ConcurrentExtractor(sample_page),
        render_pages=_render,
        progress=progress.append,
    )

    assert result.files[0].status == "partial"
    assert result.files[0].run is not None
    assert result.files[0].run.annotation_limitations
    assert json.loads(result.files[0].run.confidence_text)["review_required"]
    assert result.files[0].usage.input_tokens == 10
    assert result.files[0].cost_usd > 0
    assert result.usage == result.files[0].usage
    assert result.cost_usd == result.files[0].cost_usd
    assert progress[-1].completed_pages == 1
    assert progress[-1].failed_pages == 0


def test_batch_rejects_excessive_raster_budget(sample_page, monkeypatch) -> None:
    monkeypatch.setattr("ade_app.batch.MAX_BATCH_RASTER_PIXELS", 1)

    try:
        extract_documents(_documents(1), ConcurrentExtractor(sample_page), render_pages=_render)
    except ValueError as error:
        assert "raster budget" in str(error)
    else:
        raise AssertionError("unsafe raster budget was accepted")
