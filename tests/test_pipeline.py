from __future__ import annotations

import io
import time
from unittest.mock import Mock

import pymupdf

from ade_app.config import PipelineConfig
from ade_app.cost import TokenUsage
from ade_app.inputs import DocumentInput
from ade_app.openai_client import PageResponse, StructuredOutputError
from ade_app.orchestration import WorkflowRequest
from ade_app.pipeline import _DocumentWorkflowOperations, extract_document
from ade_app.raster import RenderedPage


def test_preprocessing_failure_retains_original_render(monkeypatch) -> None:
    page = RenderedPage(1, b"render", 10, 10)
    request = WorkflowRequest(
        DocumentInput("sample.png", b"source"), (1,), 300, rendered_pages=(page,)
    )
    monkeypatch.setattr("ade_app.pipeline.get_page_count", lambda source: 1)
    monkeypatch.setattr(
        "ade_app.pipeline.prepare_page",
        lambda rendered: (_ for _ in ()).throw(RuntimeError("orientation failed")),
    )
    operations = _DocumentWorkflowOperations(
        Mock(), max_workers=1, retry_failed_fields_with_sol=False, config=PipelineConfig()
    )

    result = operations.ingest_preprocess(request)

    assert result.rendered_pages == (page,)
    assert result.prepared_pages[0].page == page
    assert result.errors[0].message.endswith("original render retained")


class OutOfOrderExtractor:
    def __init__(self, sample_page) -> None:
        self.sample_page = sample_page

    def extract(self, page, *, job_id: str, page_count: int) -> PageResponse:
        del job_id, page_count
        time.sleep((4 - page.source_page) * 0.01)
        return PageResponse(
            extraction=self.sample_page,
            usage=TokenUsage(input_tokens=10, cached_input_tokens=2, output_tokens=3),
            response_id=f"response-{page.source_page}",
            request_id=f"request-{page.source_page}",
            service_tier="standard",
            range_repairs=0,
            usage_by_model=(
                (
                    "gpt-5.6-terra",
                    TokenUsage(input_tokens=10, cached_input_tokens=2, output_tokens=3),
                ),
            ),
            models_used=("gpt-5.6-terra",),
        )


def _three_page_pdf() -> bytes:
    document = pymupdf.open()
    for _ in range(3):
        document.new_page()
    data = document.tobytes()
    document.close()
    return data


def test_concurrent_results_are_aggregated_in_requested_order(sample_page) -> None:
    source = DocumentInput("sample.pdf", _three_page_pdf())
    png = io.BytesIO()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
    pixmap.clear_with(255)
    png.write(pixmap.tobytes("png"))
    rendered = tuple(RenderedPage(page, png.getvalue(), 10, 10) for page in (1, 2, 3))
    completion_order: list[int] = []

    run = extract_document(
        source,
        (1, 2, 3),
        OutOfOrderExtractor(sample_page),
        rendered_pages=rendered,
        progress=lambda completed, failed, total, page, status: completion_order.append(page),
    )

    assert completion_order == [3, 2, 1]
    assert [record.source_page for record in run.pages] == [1, 2, 3]
    assert [page.grounding.page for page in run.artifact.structure.children] == [1, 2, 3]
    assert run.usage.input_tokens == 30
    assert run.artifact.metadata.model_version == "gpt-5.6-terra"
    assert all(record.elapsed_ms >= 0 for record in run.pages)
    assert set(run.manifest["pages"][0]) >= {"elapsed_ms", "failure_reason"}
    assert run.manifest["job_id"] == run.artifact.metadata.job_id
    assert run.manifest["model_provider"] == "OpenAI"
    assert run.manifest["endpoint"] == "/v1/responses"
    assert run.manifest["provider_response_storage"] is False
    assert run.manifest["manifest_schema_version"] == 9
    assert run.artifact.schema_version == 3
    assert run.manifest["canonical_markdown_trusted"] is False
    assert run.manifest["evaluation_artifacts_sensitive"] is True
    assert run.manifest["review_state"] == "not_required"
    assert run.manifest["data_classification"] == "potentially_sensitive_health_document"
    assert run.manifest["governance_policy_version"] == "2026-09-01"
    assert all(
        isinstance(value, str) and len(value) == 64
        for value in run.manifest["governance_policy_sha256"].values()
    )
    prompt_hashes = run.manifest["prompt_sha256"]
    assert isinstance(prompt_hashes, dict)
    assert set(prompt_hashes) == {
        "page_extraction.md",
        "segment_consensus.md",
        "field_resolution.md",
        "region_extraction.md",
    }
    assert all(len(value) == 64 for value in prompt_hashes.values())
    assert run.manifest["review_required"] is False
    assert run.manifest["api_call_count"] == 3
    assert run.manifest["routing_call_count"] == 0
    assert run.manifest["retry_count"] == 0
    assert run.manifest["usage"]["cache_write_tokens"] == 0


def test_failed_structured_response_usage_is_included_in_run_cost() -> None:
    class FailedExtractor:
        def extract(self, page, *, job_id: str, page_count: int) -> PageResponse:
            del page, job_id, page_count
            usage = TokenUsage(input_tokens=100, cached_input_tokens=10, output_tokens=20)
            raise StructuredOutputError(2, (("gpt-5.6-terra", usage),))

    source = DocumentInput("sample.pdf", _three_page_pdf())
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
    pixmap.clear_with(255)
    rendered = (RenderedPage(1, pixmap.tobytes("png"), 10, 10),)

    run = extract_document(source, (1,), FailedExtractor(), rendered_pages=rendered)

    assert run.pages[0].status == "failed"
    assert run.pages[0].attempts == 2
    assert run.usage.input_tokens == 100
    assert run.cost_usd > 0
    assert run.manifest["review_required"] is True


def test_document_pipeline_rejects_duplicate_pages_before_model_calls(sample_page) -> None:
    extractor = OutOfOrderExtractor(sample_page)
    source = DocumentInput("sample.pdf", _three_page_pdf())

    try:
        extract_document(source, (1, 1), extractor)
    except ValueError as error:
        assert "strictly increasing" in str(error)
    else:
        raise AssertionError("duplicate pages were accepted")
