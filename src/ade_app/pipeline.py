"""Bounded concurrent, failure-isolated document extraction pipeline."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Any, Literal, Protocol, runtime_checkable

from openai import OpenAIError

from ade_app.consensus import PeerEvidence, PeerSegment, build_peer_evidence
from ade_app.constants import DEFAULT_DPI, MODEL_CASCADE
from ade_app.cost import TokenUsage, calculate_cost
from ade_app.inputs import DocumentInput
from ade_app.models import GroundTruthDocument
from ade_app.openai_client import (
    PageResponse,
    PrimaryPageResponse,
    SegmentRecord,
    active_prompt_hashes,
)
from ade_app.outputs import AnnotationLimitation, build_annotated_pdf, build_output_bundle
from ade_app.provenance import (
    artifact_hashes,
    provenance_fields,
    review_state,
    validate_document_manifest,
)
from ade_app.raster import RenderedPage, get_page_count, rasterize_document
from ade_app.rendering import PageOutcome, artifact_json, generate_job_id, render_document

ProgressCallback = Callable[[int, int, int, int, str], None]


class PageExtractor(Protocol):
    """Interface required by document and batch extraction pipelines."""

    def extract(
        self, page: RenderedPage, *, job_id: str, page_count: int
    ) -> PageResponse: ...


@runtime_checkable
class StagedPageExtractor(Protocol):
    def extract_primary(
        self, page: RenderedPage, *, job_id: str, page_count: int
    ) -> PrimaryPageResponse: ...

    def finalize(
        self,
        page: RenderedPage,
        primary: PrimaryPageResponse,
        *,
        job_id: str,
        peers: dict[str, PeerEvidence] | None = None,
    ) -> PageResponse: ...


@dataclass(frozen=True, slots=True)
class PageRunRecord:
    source_page: int
    status: Literal["ok", "failed"]
    usage: TokenUsage
    cost_usd: Decimal
    elapsed_ms: int
    response_id: str = ""
    request_id: str = ""
    range_repairs: int = 0
    failure_reason: str | None = None
    attempts: int = 1
    api_call_count: int = 1
    routing_call_count: int = 0
    retry_count: int = 0
    usage_by_model: tuple[tuple[str, TokenUsage], ...] = ()
    segments: tuple[SegmentRecord, ...] = ()
    models_used: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractionRun:
    artifact: GroundTruthDocument
    json_text: str
    markdown_filename: str
    json_filename: str
    annotated_pdf_filename: str
    zip_filename: str
    usage: TokenUsage
    cost_usd: Decimal
    pages: tuple[PageRunRecord, ...]
    annotated_pdf: bytes
    annotation_limitations: tuple[AnnotationLimitation, ...]
    manifest: dict[str, Any]

    @property
    def zip_bytes(self) -> bytes:
        """Build the individual archive only when a caller requests it."""

        return build_output_bundle(
            markdown_filename=self.markdown_filename,
            markdown=self.artifact.markdown,
            json_filename=self.json_filename,
            json_text=self.json_text,
            annotated_pdf_filename=self.annotated_pdf_filename,
            annotated_pdf=self.annotated_pdf,
            manifest=self.manifest,
        )


class DocumentExtractionError(RuntimeError):
    """Artifact finalization failed after model usage had already been incurred."""

    def __init__(
        self,
        usage: TokenUsage,
        cost_usd: Decimal,
        pages: tuple[PageRunRecord, ...],
    ) -> None:
        super().__init__("document artifact finalization failed")
        self.usage = usage
        self.cost_usd = cost_usd
        self.pages = pages


def _failed_usage(
    error: Exception,
) -> tuple[TokenUsage, tuple[tuple[str, TokenUsage], ...], Decimal]:
    usage_by_model = tuple(getattr(error, "usage_by_model", ()))
    usage = sum((item for _, item in usage_by_model), TokenUsage())
    cost = sum(
        (calculate_cost(item, model) for model, item in usage_by_model), Decimal(0)
    )
    return usage, usage_by_model, cost


def extract_document(
    source: DocumentInput,
    pages: tuple[int, ...],
    extractor: PageExtractor,
    *,
    dpi: int = DEFAULT_DPI,
    max_workers: int = 3,
    rendered_pages: tuple[RenderedPage, ...] | None = None,
    progress: ProgressCallback | None = None,
) -> ExtractionRun:
    """Extract selected pages and assemble validated, downloadable artifacts in source order."""

    page_count = get_page_count(source)
    if not pages or min(pages) < 1 or max(pages) > page_count:
        raise ValueError("selected pages are outside the document")
    if any(current <= previous for previous, current in pairwise(pages)):
        raise ValueError("selected pages must be strictly increasing and unique")

    started = time.perf_counter()
    job_id = generate_job_id()
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    rendered_pages = rendered_pages or tuple(rasterize_document(source, pages, dpi))
    if tuple(page.source_page for page in rendered_pages) != pages:
        raise ValueError("rendered pages do not match selected pages")
    outcomes_by_page: dict[int, PageOutcome] = {}
    records_by_page: dict[int, PageRunRecord] = {}
    total_usage = TokenUsage()
    service_tier = "standard"
    used_models: set[str] = set()
    total_cost = Decimal(0)
    primary_by_page: dict[int, PrimaryPageResponse] = {}
    primary_elapsed_by_page: dict[int, int] = {}
    primary_failures: dict[int, PageRunRecord] = {}
    peers = {}
    staged_extractor = extractor if isinstance(extractor, StagedPageExtractor) else None

    if staged_extractor is not None:

        def run_primary(rendered: RenderedPage) -> tuple[int, PrimaryPageResponse, int]:
            page_started = time.perf_counter()
            response = staged_extractor.extract_primary(
                rendered, job_id=job_id, page_count=page_count
            )
            return (
                rendered.source_page,
                response,
                round((time.perf_counter() - page_started) * 1_000),
            )

        with ThreadPoolExecutor(max_workers=min(max_workers, len(rendered_pages))) as pool:
            futures = {pool.submit(run_primary, page): page for page in rendered_pages}
            for future in as_completed(futures):
                rendered = futures[future]
                try:
                    source_page, response, primary_elapsed = future.result()
                    primary_by_page[source_page] = response
                    primary_elapsed_by_page[source_page] = primary_elapsed
                except (OpenAIError, RuntimeError, ValueError) as error:
                    usage, usage_by_model, cost = _failed_usage(error)
                    primary_failures[rendered.source_page] = PageRunRecord(
                        source_page=rendered.source_page,
                        status="failed",
                        usage=usage,
                        cost_usd=cost,
                        elapsed_ms=round((time.perf_counter() - started) * 1_000),
                        failure_reason=f"{type(error).__name__}: primary extraction failed",
                        attempts=int(getattr(error, "attempts", 1)),
                        api_call_count=int(getattr(error, "api_call_count", 1)),
                        routing_call_count=int(getattr(error, "routing_call_count", 0)),
                        retry_count=int(getattr(error, "retry_count", 0)),
                        usage_by_model=usage_by_model,
                        models_used=tuple(model for model, _ in usage_by_model),
                    )
        peer_segments = [
            PeerSegment(
                f"p{rendered.source_page}-s{index}",
                rendered,
                primary_by_page[rendered.source_page].state.semantic.children[index],
            )
            for rendered in rendered_pages
            if rendered.source_page in primary_by_page
            for index in range(len(primary_by_page[rendered.source_page].state.semantic.children))
        ]
        peers = build_peer_evidence(peer_segments)

    def extract_page(page: RenderedPage) -> tuple[PageOutcome, PageRunRecord, str]:
        page_started = time.perf_counter()
        try:
            if staged_extractor is not None:
                page_response = staged_extractor.finalize(
                    page,
                    primary_by_page[page.source_page],
                    job_id=job_id,
                    peers=peers,
                )
            else:
                page_response = extractor.extract(page, job_id=job_id, page_count=page_count)
            return (
                PageOutcome(source_page=page.source_page, extraction=page_response.extraction),
                PageRunRecord(
                    source_page=page.source_page,
                    status="ok",
                    response_id=page_response.response_id,
                    request_id=page_response.request_id,
                    usage=page_response.usage,
                    cost_usd=sum(
                        (
                            calculate_cost(usage, model)
                            for model, usage in page_response.usage_by_model
                        ),
                        Decimal(0),
                    ),
                    elapsed_ms=primary_elapsed_by_page.get(page.source_page, 0)
                    + round((time.perf_counter() - page_started) * 1_000),
                    range_repairs=page_response.range_repairs,
                    attempts=page_response.attempts,
                    api_call_count=page_response.api_call_count,
                    routing_call_count=page_response.routing_call_count,
                    retry_count=page_response.retry_count,
                    usage_by_model=page_response.usage_by_model,
                    segments=page_response.segments,
                    models_used=page_response.models_used,
                ),
                page_response.service_tier,
            )
        except (OpenAIError, RuntimeError, ValueError) as error:
            reason = f"{type(error).__name__}: page extraction failed"
            usage, usage_by_model, cost = _failed_usage(error)
            return (
                PageOutcome(source_page=page.source_page, failure_reason=reason),
                PageRunRecord(
                    source_page=page.source_page,
                    status="failed",
                    usage=usage,
                    cost_usd=cost,
                    elapsed_ms=round((time.perf_counter() - page_started) * 1_000),
                    failure_reason=reason,
                    attempts=int(getattr(error, "attempts", 1)),
                    api_call_count=int(getattr(error, "api_call_count", 1)),
                    routing_call_count=int(getattr(error, "routing_call_count", 0)),
                    retry_count=int(getattr(error, "retry_count", 0)),
                    usage_by_model=usage_by_model,
                    models_used=tuple(model for model, _ in usage_by_model),
                ),
                "standard",
            )

    completed = failed = 0
    for source_page, record in primary_failures.items():
        failed += 1
        completed += 1
        records_by_page[source_page] = record
        total_usage += record.usage
        total_cost += record.cost_usd
        used_models.update(record.models_used)
        outcomes_by_page[source_page] = PageOutcome(
            source_page=source_page, failure_reason=record.failure_reason
        )
        if progress is not None:
            progress(completed, failed, len(pages), source_page, "failed")
    with ThreadPoolExecutor(max_workers=min(max_workers, len(rendered_pages))) as pool:
        futures: dict[Future[tuple[PageOutcome, PageRunRecord, str]], int] = {
            pool.submit(extract_page, rendered): rendered.source_page
            for rendered in rendered_pages
            if rendered.source_page not in primary_failures
        }
        for future in as_completed(futures):
            outcome, record, page_tier = future.result()
            completed += 1
            failed += record.status == "failed"
            outcomes_by_page[record.source_page] = outcome
            records_by_page[record.source_page] = record
            total_usage += record.usage
            total_cost += record.cost_usd
            used_models.update(record.models_used)
            if page_tier == "priority":
                service_tier = "priority"
            if progress is not None:
                progress(completed, failed, len(pages), record.source_page, record.status)

    outcomes = [outcomes_by_page[page] for page in pages]
    page_records = tuple(records_by_page[page] for page in pages)

    duration_ms = round((time.perf_counter() - started) * 1_000)
    cost = total_cost
    model_version = (
        "+".join(model for model, _ in MODEL_CASCADE if model in used_models) or MODEL_CASCADE[0][0]
    )
    try:
        artifact = render_document(
            page_count=page_count,
            outcomes=outcomes,
            duration_ms=duration_ms,
            cost_usd=cost,
            job_id=job_id,
            service_tier=service_tier,
            model_version=model_version,
        )
    except Exception as error:
        raise DocumentExtractionError(total_usage, cost, page_records) from error
    markdown_filename = f"{source.stem}.parse.md"
    json_filename = f"{source.stem}.parse.json"
    annotated_pdf_filename = f"{source.stem}.annotated.pdf"
    try:
        page_nodes = {page.grounding.page: page for page in artifact.structure.children}
        untrusted_elements: dict[int, set[str]] = {}
        for record in page_records:
            page_node = page_nodes.get(record.source_page)
            if page_node is None:
                continue
            ids = {
                page_node.children[segment.segment_index].id
                for segment in record.segments
                if segment.status == "needs_review"
                and segment.segment_index < len(page_node.children)
            }
            if ids:
                untrusted_elements[record.source_page] = ids
        annotated_pdf, annotation_limitations = build_annotated_pdf(
            rendered_pages, artifact, untrusted_elements=untrusted_elements
        )
    except Exception as error:
        raise DocumentExtractionError(total_usage, cost, page_records) from error
    needs_review_segment_count = sum(
        segment.status == "needs_review"
        for record in page_records
        for segment in record.segments
    )
    api_call_count = sum(record.api_call_count for record in page_records)
    routing_call_count = sum(record.routing_call_count for record in page_records)
    retry_count = sum(record.retry_count for record in page_records)
    manifest: dict[str, object] = {
        **provenance_fields(source, rendered_pages, dpi=dpi),
        "source_filename": source.filename,
        "selected_pages": list(pages),
        "job_id": job_id,
        "model_provider": "OpenAI",
        "endpoint": "/v1/responses",
        "provider_response_storage": False,
        "model": artifact.metadata.model_version,
        "model_cascade": [
            {"model": model, "reasoning_effort": effort} for model, effort in MODEL_CASCADE
        ],
        "peer_evidence_count": len(peers),
        "prompt_sha256": active_prompt_hashes(),
        "completed_page_count": len(pages) - failed,
        "failed_page_count": failed,
        "review_required": failed > 0 or needs_review_segment_count > 0,
        "review_state": review_state(
            failed_pages=failed, unresolved_segments=needs_review_segment_count
        ),
        "needs_review_segment_count": needs_review_segment_count,
        "usage": {
            "input_tokens": total_usage.input_tokens,
            "cached_input_tokens": total_usage.cached_input_tokens,
            "cache_write_tokens": total_usage.cache_write_tokens,
            "output_tokens": total_usage.output_tokens,
            "reasoning_tokens": total_usage.reasoning_tokens,
        },
        "estimated_cost_usd": str(cost),
        "api_call_count": api_call_count,
        "routing_call_count": routing_call_count,
        "retry_count": retry_count,
        "pages": [
            {
                "source_page": record.source_page,
                "status": record.status,
                "input_tokens": record.usage.input_tokens,
                "cached_input_tokens": record.usage.cached_input_tokens,
                "cache_write_tokens": record.usage.cache_write_tokens,
                "output_tokens": record.usage.output_tokens,
                "reasoning_tokens": record.usage.reasoning_tokens,
                "estimated_cost_usd": str(record.cost_usd),
                "elapsed_ms": record.elapsed_ms,
                "range_repairs": record.range_repairs,
                "attempts": record.attempts,
                "api_call_count": record.api_call_count,
                "routing_call_count": record.routing_call_count,
                "retry_count": record.retry_count,
                "failure_reason": record.failure_reason,
                "response_id": record.response_id,
                "request_id": record.request_id,
                "models_used": list(record.models_used),
                "usage_by_model": {
                    model: {
                        "input_tokens": usage.input_tokens,
                        "cached_input_tokens": usage.cached_input_tokens,
                        "cache_write_tokens": usage.cache_write_tokens,
                        "output_tokens": usage.output_tokens,
                        "reasoning_tokens": usage.reasoning_tokens,
                        "estimated_cost_usd": str(calculate_cost(usage, model)),
                    }
                    for model, usage in record.usage_by_model
                },
                "segments": [_segment_manifest(segment) for segment in record.segments],
            }
            for record in page_records
        ],
        "annotation_limitations": [
            {
                "source_page": item.source_page,
                "element_id": item.element_id,
                "reason": item.reason,
            }
            for item in annotation_limitations
        ],
    }
    zip_filename = f"{source.stem}.outputs.zip"
    try:
        json_text = artifact_json(artifact)
        GroundTruthDocument.model_validate_json(json_text)
    except Exception as error:
        raise DocumentExtractionError(total_usage, cost, page_records) from error
    manifest["artifact_sha256"] = artifact_hashes(
        artifact.markdown, json_text, annotated_pdf
    )
    manifest = validate_document_manifest(manifest)
    return ExtractionRun(
        artifact=artifact,
        json_text=json_text,
        markdown_filename=markdown_filename,
        json_filename=json_filename,
        annotated_pdf_filename=annotated_pdf_filename,
        zip_filename=zip_filename,
        usage=total_usage,
        cost_usd=cost,
        pages=page_records,
        annotated_pdf=annotated_pdf,
        annotation_limitations=annotation_limitations,
        manifest=manifest,
    )


def _segment_manifest(segment: SegmentRecord) -> dict[str, object]:
    return {
        "segment_id": segment.segment_id,
        "segment_index": segment.segment_index,
        "final_score": segment.final_score,
        "status": segment.status,
        "reasons": list(segment.reasons),
        "structural_conflicts": list(segment.structural_conflicts),
        "unresolved_fields": list(segment.unresolved_fields),
        "attempts": [
            {
                "model": attempt.model,
                "reasoning_effort": attempt.effort,
                "score": attempt.score,
                "accepted": attempt.accepted,
                "input_tokens": attempt.usage.input_tokens,
                "cached_input_tokens": attempt.usage.cached_input_tokens,
                "cache_write_tokens": attempt.usage.cache_write_tokens,
                "output_tokens": attempt.usage.output_tokens,
                "reasoning_tokens": attempt.usage.reasoning_tokens,
                "estimated_cost_usd": str(calculate_cost(attempt.usage, attempt.model)),
                "response_id": attempt.response_id,
                "request_id": attempt.request_id,
                "failure_reason": attempt.failure_reason,
                "stage": attempt.stage,
                "peer_source_page": attempt.peer_source_page,
                "disagreement_count": attempt.disagreement_count,
                "batch_index": attempt.batch_index,
                "api_call_count": attempt.api_call_count,
                "retry_count": attempt.retry_count,
            }
            for attempt in segment.attempts
        ],
    }
