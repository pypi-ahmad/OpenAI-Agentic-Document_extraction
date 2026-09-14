"""Bounded concurrent, failure-isolated document extraction pipeline.

Responsible for orchestrating single-document extraction workflows,
managing concurrent page execution, isolating per-page failures, linking structured fields,
and compiling final `ExtractionRun` results.
Must NOT manage multi-document batch queues or discard successful pages on partial errors.
Next: ade_app.batch for multi-document batch coordination, or ade_app.outputs for artifact bundling.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from decimal import Decimal
from itertools import pairwise
from threading import Lock
from typing import Any, Protocol, runtime_checkable

from openai import OpenAIError

from ade_app.config import PipelineConfig
from ade_app.consensus import PeerEvidence, PeerSegment, build_peer_evidence
from ade_app.constants import DEFAULT_DPI
from ade_app.contracts import ExtractionRun, PageRunRecord
from ade_app.cost import TokenUsage, calculate_cost
from ade_app.fields import (
    apply_field_resolutions,
    discover_raw_fields,
    link_and_validate_fields,
    public_fields,
)
from ade_app.inputs import DocumentInput
from ade_app.layout import LayoutAnalysis
from ade_app.models import ExtractionDocumentV3
from ade_app.openai_client import (
    PageResponse,
    PrimaryPageResponse,
    SegmentRecord,
    active_prompt_hashes,
)
from ade_app.orchestration import (
    DocumentWorkflowState,
    ExtractionStageResult,
    IngestionStageResult,
    LayoutPageResult,
    LayoutStageResult,
    SolRetryPlan,
    ValidationStageResult,
    WorkflowConfig,
    WorkflowRequest,
    run_document_workflow,
    run_page_workflow,
)
from ade_app.outputs import (
    AnnotationLimitation,
    build_annotated_pdf,
    build_annotation_failure_pdf,
)
from ade_app.provenance import (
    artifact_hashes,
    provenance_fields,
    review_state,
    validate_document_manifest,
)
from ade_app.raster import RenderedPage, get_page_count, rasterize_document
from ade_app.rendering import PageOutcome, artifact_json, generate_job_id, render_document
from ade_app.services.confidence import build_confidence_report
from ade_app.services.imaging import (
    PageIngestionError,
    PageTransform,
    PreparedPage,
    ingest_document,
    prepare_page,
)

ProgressCallback = Callable[[int, int, int, int, str], None]
logger = logging.getLogger(__name__)


class PageExtractor(Protocol):
    """Interface required by document and batch extraction pipelines."""

    def extract(self, page: RenderedPage, *, job_id: str, page_count: int) -> PageResponse: ...


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
        allow_sol: bool = True,
    ) -> PageResponse: ...


@runtime_checkable
class LayoutAwarePageExtractor(Protocol):
    def extract_primary_from_layout(
        self,
        page: RenderedPage,
        prepared: PreparedPage,
        analysis: LayoutAnalysis | None,
        *,
        job_id: str,
        page_count: int,
    ) -> PrimaryPageResponse: ...


@runtime_checkable
class PostProcessingResolver(Protocol):
    def resolve_postprocessing_fields(
        self,
        fields: list,
        pages: tuple[RenderedPage, ...],
        *,
        job_id: str,
    ) -> Any: ...


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
    cost = sum((calculate_cost(item, model) for model, item in usage_by_model), Decimal(0))
    return usage, usage_by_model, cost


def _route_and_extract_impl(
    source: DocumentInput,
    pages: tuple[int, ...],
    extractor: PageExtractor,
    *,
    dpi: int = DEFAULT_DPI,
    max_workers: int = 3,
    rendered_pages: tuple[RenderedPage, ...] | None = None,
    progress: ProgressCallback | None = None,
    retry_failed_fields_with_sol: bool = False,
    _job_id: str | None = None,
    _page_failures: dict[int, str] | None = None,
    _layout: LayoutStageResult | None = None,
    _config: PipelineConfig | None = None,
) -> ExtractionStageResult:
    """Run page-level semantic routing and return raw, ordered extraction evidence."""

    settings = _config or PipelineConfig()
    cascade = tuple(
        (model.name, model.reasoning_effort)
        for model in (settings.models.luna, settings.models.terra, settings.models.sol)
    )
    page_count = get_page_count(source)
    if not pages or min(pages) < 1 or max(pages) > page_count:
        raise ValueError("selected pages are outside the document")
    if any(current <= previous for previous, current in pairwise(pages)):
        raise ValueError("selected pages must be strictly increasing and unique")

    started = time.perf_counter()
    job_id = _job_id or generate_job_id()
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    rendered_pages = rendered_pages or tuple(rasterize_document(source, pages, dpi))
    rendered_page_numbers = tuple(page.source_page for page in rendered_pages)
    if any(page not in pages for page in rendered_page_numbers) or len(
        rendered_page_numbers
    ) != len(set(rendered_page_numbers)):
        raise ValueError("rendered pages do not match selected pages")
    outcomes_by_page: dict[int, PageOutcome] = {}
    records_by_page: dict[int, PageRunRecord] = {}
    total_usage = TokenUsage()
    service_tier = "standard"
    used_models: set[str] = set()
    total_cost = Decimal(0)
    primary_by_page: dict[int, PrimaryPageResponse] = {}
    primary_elapsed_by_page: dict[int, int] = {}
    page_failures = _page_failures or {}
    primary_failures: dict[int, PageRunRecord] = {
        page: PageRunRecord(
            source_page=page,
            status="failed",
            usage=TokenUsage(),
            cost_usd=Decimal(0),
            elapsed_ms=0,
            failure_reason=page_failures.get(page, "page ingestion failed"),
            attempts=0,
            api_call_count=0,
        )
        for page in pages
        if page not in rendered_page_numbers
    }
    peers = {}
    staged_extractor = extractor if isinstance(extractor, StagedPageExtractor) else None
    layout_extractor = extractor if isinstance(extractor, LayoutAwarePageExtractor) else None
    layout_by_page = {
        item.prepared.page.source_page: item for item in (_layout.pages if _layout else ())
    }

    if staged_extractor is not None:

        def run_primary(rendered: RenderedPage) -> tuple[int, PrimaryPageResponse, int]:
            page_started = time.perf_counter()
            layout_page = layout_by_page.get(rendered.source_page)
            if layout_extractor is not None and layout_page is not None:
                response = layout_extractor.extract_primary_from_layout(
                    rendered,
                    layout_page.prepared,
                    layout_page.analysis,
                    job_id=job_id,
                    page_count=page_count,
                )
            else:
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
                    allow_sol=retry_failed_fields_with_sol,
                )
            else:
                page_response = extractor.extract(page, job_id=job_id, page_count=page_count)
            return (
                PageOutcome(
                    source_page=page.source_page,
                    extraction=page_response.extraction,
                    candidate_extraction=page_response.candidate_extraction,
                ),
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
                    full_page_fallback=page_response.full_page_fallback,
                    layout_issues=page_response.layout_issues,
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
    pending_pages = tuple(
        page for page in rendered_pages if page.source_page not in primary_failures
    )
    progress_lock = Lock()

    def extract_page_and_report(page: RenderedPage):
        nonlocal completed, failed
        result = extract_page(page)
        if progress is not None:
            with progress_lock:
                completed += 1
                failed += result[1].status == "failed"
                progress(completed, failed, len(pages), result[1].source_page, result[1].status)
        return result

    for outcome, record, page_tier in run_page_workflow(pending_pages, extract_page_and_report):
        if progress is None:
            completed += 1
            failed += record.status == "failed"
        outcomes_by_page[record.source_page] = outcome
        records_by_page[record.source_page] = record
        total_usage += record.usage
        total_cost += record.cost_usd
        used_models.update(record.models_used)
        if page_tier == "priority":
            service_tier = "priority"

    outcomes = [outcomes_by_page[page] for page in pages]
    page_records = tuple(records_by_page[page] for page in pages)

    duration_ms = round((time.perf_counter() - started) * 1_000)
    cost = total_cost
    model_version = "+".join(model for model, _ in cascade if model in used_models) or cascade[0][0]
    return ExtractionStageResult(
        outcomes=tuple(outcomes),
        page_records=page_records,
        raw_fields=discover_raw_fields(outcomes, page_records, rendered_pages or ()),
        usage=total_usage,
        cost_usd=cost,
        service_tier=service_tier,
        model_version=model_version,
        peer_evidence_count=len(peers),
        duration_ms=duration_ms,
    )


_CRITICAL_FIELDS = {"name", "patient_name", "member_name", "npi", "member_id", "dob"}


def _validate_and_link_impl(
    extraction: ExtractionStageResult,
    *,
    review_threshold: float,
    retry_failed_fields_with_sol: bool,
    max_sol_fields: int,
) -> ValidationStageResult:
    fields = link_and_validate_fields(extraction.raw_fields, review_threshold=review_threshold)
    if extraction.sol_resolutions:
        fields = apply_field_resolutions(
            fields,
            extraction.sol_resolutions,
            review_threshold=review_threshold,
        )

    element_ids: dict[tuple[int, int], str] = {}
    counters: Counter[str] = Counter()
    for outcome in extraction.outcomes:
        if outcome.extraction is None:
            continue
        for index, element in enumerate(outcome.extraction.children):
            element_ids[(outcome.source_page, index)] = f"{element.type}-{counters[element.type]}"
            counters[element.type] += 1

    untrusted: dict[int, set[str]] = {}
    for record in extraction.page_records:
        ids = {
            element_ids[(record.source_page, segment.segment_index)]
            for segment in record.segments
            if segment.status == "needs_review"
            and (record.source_page, segment.segment_index) in element_ids
        }
        if ids:
            untrusted[record.source_page] = ids

    for field in fields:
        if field.status == "accepted":
            continue
        for evidence in field.evidence:
            untrusted.setdefault(evidence.page, set()).add(evidence.region_id)

    retry_plan = None
    if retry_failed_fields_with_sol and extraction.sol_resolutions is None:
        mandatory = [
            field
            for field in fields
            if "verification_attempted" not in field.reasons
            and not any(evidence.route == "sol" for evidence in field.evidence)
            and (
                field.canonical_name in _CRITICAL_FIELDS
                or field.confidence < 75
                or any(not check.passed for check in field.validation_checks)
            )
        ]
        optional = [
            field
            for field in fields
            if field.status == "conflict" and "verification_attempted" not in field.reasons
        ]
        target_ids = list(dict.fromkeys(field.field_id for field in (*mandatory, *optional)))
        by_id = {field.field_id: field for field in fields}
        selected = tuple(by_id[field_id] for field_id in target_ids[:max_sol_fields])
        if selected:
            retry_plan = SolRetryPlan(
                mandatory_region_ids=tuple(
                    dict.fromkeys(
                        evidence.region_id for field in mandatory for evidence in field.evidence
                    )
                ),
                fields=selected,
            )

    return ValidationStageResult(
        fields=tuple(fields),
        untrusted_elements=untrusted,
        needs_review_segment_count=sum(
            segment.status == "needs_review"
            for record in extraction.page_records
            for segment in record.segments
        ),
        needs_review_field_count=sum(field.status != "accepted" for field in fields),
        retry_plan=retry_plan,
    )


def _generate_outputs_impl(
    request: WorkflowRequest,
    ingestion: IngestionStageResult,
    extraction: ExtractionStageResult,
    validation: ValidationStageResult,
    *,
    job_id: str,
    cascade: tuple[tuple[str, str], ...],
) -> ExtractionRun:
    failed = sum(record.status == "failed" for record in extraction.page_records)
    base = render_document(
        page_count=ingestion.page_count,
        outcomes=list(extraction.outcomes),
        duration_ms=extraction.duration_ms,
        cost_usd=extraction.cost_usd,
        job_id=job_id,
        service_tier=extraction.service_tier,
        model_version=extraction.model_version,
    )
    artifact = ExtractionDocumentV3(
        markdown=base.markdown,
        metadata=base.metadata,
        structure=base.structure,
        fields=public_fields(validation.fields),
    )
    try:
        annotated_pdf, limitations = build_annotated_pdf(
            ingestion.rendered_pages,
            artifact,
            untrusted_elements=validation.untrusted_elements,
        )
    except Exception as error:
        logger.warning("Annotated PDF generation failed", exc_info=error)
        annotated_pdf = build_annotation_failure_pdf()
        limitations = tuple(
            AnnotationLimitation(page, None, "annotated PDF generation failed")
            for page in request.pages
        )

    source = request.source
    manifest: dict[str, object] = {
        **provenance_fields(source, ingestion.rendered_pages, dpi=request.dpi),
        "source_filename": source.filename,
        "selected_pages": list(request.pages),
        "job_id": job_id,
        "model_provider": "OpenAI",
        "endpoint": "/v1/responses",
        "provider_response_storage": False,
        "model": artifact.metadata.model_version,
        "model_cascade": [
            {"model": model, "reasoning_effort": effort} for model, effort in cascade
        ],
        "peer_evidence_count": extraction.peer_evidence_count,
        "prompt_sha256": active_prompt_hashes(),
        "completed_page_count": len(request.pages) - failed,
        "failed_page_count": failed,
        "review_required": bool(limitations)
        or failed > 0
        or validation.needs_review_segment_count > 0
        or validation.needs_review_field_count > 0,
        "review_state": review_state(
            failed_pages=failed,
            unresolved_segments=(
                validation.needs_review_segment_count
                + validation.needs_review_field_count
                + len(limitations)
            ),
        ),
        "needs_review_segment_count": validation.needs_review_segment_count,
        "needs_review_field_count": validation.needs_review_field_count,
        "artifact_schema_version": 3,
        "preprocessing": {
            "renderer": "PyMuPDF",
            "opencv_version": "5.0.0",
            "conditional_transforms": ["rotation", "deskew", "denoise", "contrast"],
        },
        "routing": {
            "layout_engine": "PP-StructureV3",
            "form_detection": "OpenCV geometry + Terra semantics",
            "policy": "calibrated_fail_closed",
            "full_page_fallback_count": sum(
                record.full_page_fallback is True for record in extraction.page_records
            ),
        },
        "usage": {
            "input_tokens": extraction.usage.input_tokens,
            "cached_input_tokens": extraction.usage.cached_input_tokens,
            "cache_write_tokens": extraction.usage.cache_write_tokens,
            "output_tokens": extraction.usage.output_tokens,
            "reasoning_tokens": extraction.usage.reasoning_tokens,
        },
        "estimated_cost_usd": str(extraction.cost_usd),
        "api_call_count": sum(record.api_call_count for record in extraction.page_records),
        "routing_call_count": sum(record.routing_call_count for record in extraction.page_records),
        "retry_count": sum(record.retry_count for record in extraction.page_records),
        "pages": [_page_manifest(record) for record in extraction.page_records],
        "annotation_limitations": [
            {
                "source_page": item.source_page,
                "element_id": item.element_id,
                "reason": item.reason,
            }
            for item in limitations
        ],
    }
    json_text = artifact_json(artifact)
    ExtractionDocumentV3.model_validate_json(json_text)
    confidence_report = build_confidence_report(extraction.page_records, artifact)
    if limitations:
        confidence_report.review_required = True
        for page in confidence_report.pages:
            page.review_reasons.extend(
                item.reason for item in limitations if item.source_page == page.source_page
            )
    confidence_text = confidence_report.model_dump_json(indent=2)
    manifest["artifact_sha256"] = artifact_hashes(artifact.markdown, json_text, annotated_pdf)
    manifest = validate_document_manifest(manifest)
    return ExtractionRun(
        artifact=artifact,
        json_text=json_text,
        markdown_filename=f"{source.stem}.parse.md",
        json_filename=f"{source.stem}.parse.json",
        confidence_text=confidence_text,
        confidence_filename=f"{source.stem}.confidence.json",
        annotated_pdf_filename=f"{source.stem}.annotated.pdf",
        zip_filename=f"{source.stem}.outputs.zip",
        usage=extraction.usage,
        cost_usd=extraction.cost_usd,
        pages=extraction.page_records,
        annotated_pdf=annotated_pdf,
        annotation_limitations=limitations,
        manifest=manifest,
    )


def _page_manifest(record: PageRunRecord) -> dict[str, object]:
    return {
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
        "full_page_fallback": record.full_page_fallback,
        "layout_issues": list(record.layout_issues),
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


def _segment_manifest(segment: SegmentRecord) -> dict[str, object]:
    return {
        "segment_id": segment.segment_id,
        "segment_index": segment.segment_index,
        "final_score": segment.final_score,
        "status": segment.status,
        "reasons": list(segment.reasons),
        "structural_conflicts": list(segment.structural_conflicts),
        "unresolved_fields": list(segment.unresolved_fields),
        "final_route": segment.final_route,
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


class _DocumentWorkflowOperations:
    """Production effects behind typed LangGraph stage nodes."""

    def __init__(
        self,
        extractor: PageExtractor,
        *,
        max_workers: int,
        retry_failed_fields_with_sol: bool,
        config: PipelineConfig,
    ) -> None:
        self.extractor = extractor
        self.max_workers = max_workers
        self.retry_failed_fields_with_sol = retry_failed_fields_with_sol
        self.config = config
        self.job_id = generate_job_id()
        self.terminal_error: Exception | None = None

    def ingest_preprocess(self, request: WorkflowRequest) -> IngestionStageResult:
        page_count = get_page_count(request.source)
        if (
            not request.pages
            or min(request.pages) < 1
            or max(request.pages) > page_count
            or any(right <= left for left, right in pairwise(request.pages))
        ):
            raise ValueError("selected pages are outside the document or not strictly increasing")
        if request.rendered_pages is not None:
            prepared = []
            errors = []
            for page in request.rendered_pages:
                try:
                    prepared.append(prepare_page(page))
                except Exception as error:
                    prepared.append(
                        PreparedPage(
                            original=page,
                            page=page,
                            transform=PageTransform(
                                forward=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                inverse=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                operations=[],
                            ),
                        )
                    )
                    errors.append(
                        PageIngestionError(
                            source_page=page.source_page,
                            stage="preprocess",
                            message="Page preprocessing failed; original render retained",
                            cause_type=type(error).__name__,
                        )
                    )
            return IngestionStageResult(
                page_count=page_count,
                prepared_pages=tuple(prepared),
                rendered_pages=tuple(page.original for page in prepared),
                errors=tuple(errors),
            )
        result = ingest_document(request.source, request.pages, request.dpi)
        if not result.pages:
            message = result.errors[0].message if result.errors else "no pages were ingested"
            raise RuntimeError(message)
        return IngestionStageResult(
            page_count=page_count,
            prepared_pages=result.pages,
            rendered_pages=tuple(page.original for page in result.pages),
            errors=result.errors,
        )

    def layout_analysis(self, ingestion: IngestionStageResult) -> LayoutStageResult:
        analyze = getattr(self.extractor, "analyze_prepared", None)
        if not callable(analyze):
            return LayoutStageResult(pages=())
        return LayoutStageResult(
            pages=tuple(
                LayoutPageResult(prepared=prepared, analysis=analyze(prepared))
                for prepared in ingestion.prepared_pages
            )
        )

    def route_and_extract(
        self,
        layout: LayoutStageResult,
        ingestion: IngestionStageResult,
        previous: ExtractionStageResult | None,
        retry_plan: SolRetryPlan | None,
    ) -> ExtractionStageResult:
        try:
            if previous is not None and retry_plan is not None:
                return self._resolve_with_sol(previous, retry_plan, ingestion.rendered_pages)
            request = self._request
            failures = {
                error.source_page: error.message
                for error in ingestion.errors
                if error.source_page is not None
            }
            return _route_and_extract_impl(
                request.source,
                request.pages,
                self.extractor,
                dpi=request.dpi,
                max_workers=self.max_workers,
                rendered_pages=ingestion.rendered_pages,
                progress=request.progress,
                retry_failed_fields_with_sol=self.retry_failed_fields_with_sol,
                _job_id=self.job_id,
                _page_failures=failures,
                _layout=layout,
                _config=self.config,
            )
        except Exception as error:
            self.terminal_error = error
            raise

    def validate_and_link(self, extraction: ExtractionStageResult) -> ValidationStageResult:
        return _validate_and_link_impl(
            extraction,
            review_threshold=self.config.routing.field_review_threshold_percent,
            retry_failed_fields_with_sol=self.retry_failed_fields_with_sol,
            max_sol_fields=self.config.routing.max_sol_fields_per_document,
        )

    def generate_outputs(
        self,
        ingestion: IngestionStageResult,
        extraction: ExtractionStageResult,
        validation: ValidationStageResult,
    ) -> ExtractionRun:
        cascade = tuple(
            (model.name, model.reasoning_effort)
            for model in (
                self.config.models.luna,
                self.config.models.terra,
                self.config.models.sol,
            )
        )
        return _generate_outputs_impl(
            self._request,
            ingestion,
            extraction,
            validation,
            job_id=self.job_id,
            cascade=cascade,
        )

    def bind_request(self, request: WorkflowRequest) -> None:
        self._request = request

    def _resolve_with_sol(
        self,
        previous: ExtractionStageResult,
        plan: SolRetryPlan,
        pages: tuple[RenderedPage, ...],
    ) -> ExtractionStageResult:
        resolver = self.extractor
        if not isinstance(resolver, PostProcessingResolver):
            return replace(previous, sol_resolutions={})
        result = resolver.resolve_postprocessing_fields(
            list(plan.fields), pages, job_id=self.job_id
        )
        model = self.config.models.sol.name
        usage = result.usage
        cost = calculate_cost(usage, model)
        records = list(previous.page_records)
        if records:
            index = next(
                (
                    i
                    for i, record in enumerate(records)
                    if record.source_page == plan.fields[0].evidence[0].page
                ),
                0,
            )
            record = records[index]
            records[index] = replace(
                record,
                usage=record.usage + usage,
                cost_usd=record.cost_usd + cost,
                api_call_count=record.api_call_count + result.api_call_count,
                retry_count=record.retry_count + result.retry_count,
                usage_by_model=(*record.usage_by_model, (model, usage)),
                models_used=tuple(dict.fromkeys((*record.models_used, model))),
            )
        return replace(
            previous,
            page_records=tuple(records),
            usage=previous.usage + usage,
            cost_usd=previous.cost_usd + cost,
            model_version="+".join(
                dict.fromkeys(filter(None, (*previous.model_version.split("+"), model)))
            ),
            sol_resolutions=result.resolutions,
        )


def extract_document(
    source: DocumentInput,
    pages: tuple[int, ...],
    extractor: PageExtractor,
    *,
    dpi: int = DEFAULT_DPI,
    max_workers: int = 3,
    rendered_pages: tuple[RenderedPage, ...] | None = None,
    progress: ProgressCallback | None = None,
    retry_failed_fields_with_sol: bool = False,
    max_graph_retries: int = 1,
    config: PipelineConfig | None = None,
) -> ExtractionRun:
    """Run complete extraction through authoritative in-memory LangGraph."""

    page_count = get_page_count(source)
    if (
        not pages
        or min(pages) < 1
        or max(pages) > page_count
        or any(right <= left for left, right in pairwise(pages))
    ):
        raise ValueError("selected pages are outside the document or not strictly increasing")

    pipeline_config = config or PipelineConfig()
    workflow_config = WorkflowConfig(
        max_graph_retries=max_graph_retries,
        max_concurrency=max_workers,
        retry_failed_fields_with_sol=retry_failed_fields_with_sol,
    )
    request = WorkflowRequest(
        source=source,
        pages=pages,
        dpi=dpi,
        rendered_pages=rendered_pages,
        progress=progress,
    )
    operations = _DocumentWorkflowOperations(
        extractor,
        max_workers=max_workers,
        retry_failed_fields_with_sol=retry_failed_fields_with_sol,
        config=pipeline_config,
    )
    operations.bind_request(request)
    try:
        final = run_document_workflow(
            DocumentWorkflowState(
                operations=operations,
                request=request,
                config=workflow_config,
            )
        )
        if isinstance(final.output, ExtractionRun):
            return final.output
        if isinstance(operations.terminal_error, DocumentExtractionError):
            raise operations.terminal_error
        raise RuntimeError(final.errors[-1].message if final.errors else "document workflow failed")
    finally:
        cleanup = getattr(extractor, "end_document", None)
        if cleanup is not None:
            cleanup(operations.job_id)
