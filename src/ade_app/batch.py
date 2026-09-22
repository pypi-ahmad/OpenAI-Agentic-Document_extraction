"""Bounded multi-document orchestration around the existing document pipeline.

Responsible for enforcing batch-wide limits (file count, byte size, page
count, raster-pixel budget, unique item IDs, ordered pages) and for running
up to MAX_FILE_WORKERS documents concurrently, each with up to
MAX_PAGE_WORKERS_PER_DOCUMENT page workers. This is a distinct, larger
concurrency bound than the process-wide OpenAI call semaphore enforced in
ade_app.openai_client — open that module next to see how individual API
calls are throttled underneath this layer. Must NOT perform rasterization or
extraction itself; both are delegated (render_pages / ade_app.pipeline).
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise
from typing import Literal

from ade_app.config import PipelineConfig
from ade_app.constants import DEFAULT_DPI
from ade_app.cost import TokenUsage
from ade_app.inputs import DocumentInput
from ade_app.outputs import BatchBundleEntry, build_batch_output_bundle
from ade_app.pipeline import ExtractionRun, PageExtractor, extract_document
from ade_app.provenance import governance_policy_fields, review_state, validate_batch_manifest
from ade_app.raster import (
    MAX_BATCH_RASTER_PIXELS,
    RenderedPage,
    estimate_render_pixels,
    rasterize_document,
)

MAX_BATCH_FILES = 20
MAX_BATCH_BYTES = 500 * 1024 * 1024
MAX_BATCH_PAGES = 100
MAX_FILE_WORKERS = 4
MAX_PAGE_WORKERS_PER_DOCUMENT = 2


@dataclass(frozen=True, slots=True)
class BatchDocument:
    """One validated input and its ordered, one-based page selection within a batch."""

    item_id: str
    source: DocumentInput
    pages: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class BatchProgress:
    completed_pages: int
    failed_pages: int
    total_pages: int
    completed_files: int
    failed_files: int
    total_files: int
    item_id: str
    filename: str
    source_page: int | None
    status: str


@dataclass(frozen=True, slots=True)
class BatchFileResult:
    item_id: str
    source_filename: str
    selected_pages: tuple[int, ...]
    run: ExtractionRun | None
    failure_reason: str | None = None
    failed_usage: TokenUsage = field(default_factory=TokenUsage)
    failed_cost_usd: Decimal = Decimal(0)
    failed_api_call_count: int = 0
    failed_routing_call_count: int = 0
    failed_retry_count: int = 0

    @property
    def status(self) -> Literal["ok", "partial", "failed"]:
        if self.run is None:
            return "failed"
        if (
            any(page.status == "failed" for page in self.run.pages)
            or self.run.manifest.get("review_required")
            or self.run.annotation_limitations
        ):
            return "partial"
        return "ok"

    @property
    def usage(self) -> TokenUsage:
        return self.run.usage if self.run is not None else self.failed_usage

    @property
    def cost_usd(self) -> Decimal:
        return self.run.cost_usd if self.run is not None else self.failed_cost_usd

    @property
    def api_call_count(self) -> int:
        return (
            sum(page.api_call_count for page in self.run.pages)
            if self.run is not None
            else self.failed_api_call_count
        )

    @property
    def routing_call_count(self) -> int:
        return (
            sum(page.routing_call_count for page in self.run.pages)
            if self.run is not None
            else self.failed_routing_call_count
        )

    @property
    def retry_count(self) -> int:
        return (
            sum(page.retry_count for page in self.run.pages)
            if self.run is not None
            else self.failed_retry_count
        )


@dataclass(frozen=True, slots=True)
class BatchExtractionRun:
    files: tuple[BatchFileResult, ...]
    usage: TokenUsage
    cost_usd: Decimal
    manifest: dict[str, object]
    zip_filename: str
    zip_bytes: bytes


BatchProgressCallback = Callable[[BatchProgress], None]
PageRenderer = Callable[[DocumentInput, tuple[int, ...], int], tuple[RenderedPage, ...]]


def extract_documents(
    documents: tuple[BatchDocument, ...],
    extractor: PageExtractor,
    *,
    max_file_workers: int = MAX_FILE_WORKERS,
    dpi: int = DEFAULT_DPI,
    render_pages: PageRenderer | None = None,
    progress: BatchProgressCallback | None = None,
    retry_failed_fields: bool | None = None,
    max_graph_retries: int = 1,
    config: PipelineConfig | None = None,
) -> BatchExtractionRun:
    """Run documents and pages concurrently within explicit production bounds."""

    # Batch-level limits are all checked up front, before any rendering or
    # extraction starts, so an oversized/invalid batch fails fast instead of
    # burning API calls on some documents before rejecting the batch.
    if not documents:
        raise ValueError("at least one document is required")
    if len(documents) > MAX_BATCH_FILES:
        raise ValueError(f"a batch may contain at most {MAX_BATCH_FILES} files")
    if len({document.item_id for document in documents}) != len(documents):
        raise ValueError("batch item IDs must be unique")
    if sum(len(document.source.data) for document in documents) > MAX_BATCH_BYTES:
        raise ValueError("total upload size may not exceed 500 MB")
    if sum(len(document.pages) for document in documents) > MAX_BATCH_PAGES:
        raise ValueError(f"a batch may contain at most {MAX_BATCH_PAGES} selected pages")
    if max_file_workers < 1 or max_file_workers > MAX_FILE_WORKERS:
        raise ValueError(f"max_file_workers must be between 1 and {MAX_FILE_WORKERS}")
    for document in documents:
        if not document.pages:
            raise ValueError("every document requires at least one selected page")
        if any(current <= previous for previous, current in pairwise(document.pages)):
            raise ValueError("selected pages must be strictly increasing and unique")
    estimated_pixels = sum(
        estimate_render_pixels(document.source, document.pages, dpi) for document in documents
    )
    if estimated_pixels > MAX_BATCH_RASTER_PIXELS:
        raise ValueError(
            f"selected pages exceed the {MAX_BATCH_RASTER_PIXELS:,}-pixel raster budget"
        )

    renderer = render_pages or _render_pages
    # extract_document's own progress callback fires from worker threads (one
    # per document, via the pool below). progress() is caller-supplied UI code
    # (e.g. Streamlit) that must only run on this thread, so page-level events
    # are queued here and drained from the main loop instead of being called
    # directly from a worker thread.
    events: queue.Queue[tuple[str, int, str]] = queue.Queue()
    by_id = {document.item_id: document for document in documents}
    page_statuses: dict[tuple[str, int], str] = {}
    total_pages = sum(len(document.pages) for document in documents)
    completed_pages = failed_pages = completed_files = failed_files = 0
    results: dict[str, BatchFileResult] = {}

    def notify(document: BatchDocument, source_page: int | None, status: str) -> None:
        if progress is not None:
            progress(
                BatchProgress(
                    completed_pages,
                    failed_pages,
                    total_pages,
                    completed_files,
                    failed_files,
                    len(documents),
                    document.item_id,
                    document.source.filename,
                    source_page,
                    status,
                )
            )

    def record_page_status(item_id: str, source_page: int, status: str) -> None:
        nonlocal completed_pages, failed_pages
        marker = (item_id, source_page)
        previous = page_statuses.get(marker)
        if previous is None:
            completed_pages += 1
        if previous != "failed" and status == "failed":
            failed_pages += 1
        elif previous == "failed" and status != "failed":
            failed_pages -= 1
        page_statuses[marker] = status
        notify(by_id[item_id], source_page, status)

    def drain_events() -> None:
        while True:
            try:
                item_id, source_page, status = events.get_nowait()
            except queue.Empty:
                return
            record_page_status(item_id, source_page, status)

    def run_document(document: BatchDocument) -> ExtractionRun:
        rendered = (
            renderer(document.source, document.pages, dpi) if render_pages is not None else None
        )
        return extract_document(
            document.source,
            document.pages,
            extractor,
            dpi=dpi,
            max_workers=MAX_PAGE_WORKERS_PER_DOCUMENT,
            rendered_pages=rendered,
            progress=lambda _completed, _failed, _total, page, status: events.put(
                (document.item_id, page, status)
            ),
            retry_failed_fields=retry_failed_fields,
            max_graph_retries=max_graph_retries,
            config=config,
        )

    with ThreadPoolExecutor(max_workers=min(max_file_workers, len(documents))) as pool:
        futures: dict[Future[ExtractionRun], BatchDocument] = {
            pool.submit(run_document, document): document for document in documents
        }
        pending = set(futures)
        while pending:
            done, pending = wait(pending, timeout=0.1)
            drain_events()
            for future in done:
                document = futures[future]
                try:
                    run = future.result()
                    results[document.item_id] = BatchFileResult(
                        document.item_id, document.source.filename, document.pages, run
                    )
                    completed_files += 1
                    notify(document, None, "complete")
                except Exception as error:  # Isolate one document from the rest of the batch.
                    for page in document.pages:
                        record_page_status(document.item_id, page, "failed")
                    results[document.item_id] = BatchFileResult(
                        document.item_id,
                        document.source.filename,
                        document.pages,
                        None,
                        f"{type(error).__name__}: document processing failed",
                        getattr(error, "usage", TokenUsage()),
                        getattr(error, "cost_usd", Decimal(0)),
                        sum(page.api_call_count for page in getattr(error, "pages", ())),
                        sum(page.routing_call_count for page in getattr(error, "pages", ())),
                        sum(page.retry_count for page in getattr(error, "pages", ())),
                    )
                    completed_files += 1
                    failed_files += 1
                    notify(document, None, "failed")
        drain_events()

    ordered = tuple(results[document.item_id] for document in documents)
    usage = TokenUsage()
    cost = Decimal(0)
    entries: list[BatchBundleEntry] = []
    for index, result in enumerate(ordered, 1):
        usage += result.usage
        cost += result.cost_usd
        if result.run is None:
            continue
        entries.append(
            BatchBundleEntry(
                folder=f"{index:02d}_{_safe_stem(result.source_filename)}",
                markdown_filename=result.run.markdown_filename,
                markdown=result.run.artifact.markdown,
                json_filename=result.run.json_filename,
                json_text=result.run.json_text,
                confidence_filename=result.run.confidence_filename,
                confidence_text=result.run.confidence_text,
                annotated_pdf_filename=result.run.annotated_pdf_filename,
                annotated_pdf=result.run.annotated_pdf,
                manifest=result.run.manifest,
                extra_files=result.run.draft_files,
            )
        )
    ok_count = sum(result.status == "ok" for result in ordered)
    partial_count = sum(result.status == "partial" for result in ordered)
    hard_failure_count = sum(result.status == "failed" for result in ordered)
    review_required_count = sum(
        result.run is None or bool(result.run.manifest["review_required"]) for result in ordered
    )
    page_failure_count = sum(
        len(result.selected_pages)
        if result.run is None
        else sum(page.status == "failed" for page in result.run.pages)
        for result in ordered
    )
    api_call_count = sum(result.api_call_count for result in ordered)
    routing_call_count = sum(result.routing_call_count for result in ordered)
    retry_count = sum(result.retry_count for result in ordered)
    manifest: dict[str, object] = {
        **governance_policy_fields(),
        "file_count": len(ordered),
        "successful_file_count": ok_count,
        "partial_file_count": partial_count,
        "failed_file_count": hard_failure_count,
        "review_required": review_required_count > 0,
        "review_state": review_state(
            failed_pages=page_failure_count,
            unresolved_segments=review_required_count - hard_failure_count,
        ),
        "review_required_file_count": review_required_count,
        "selected_page_count": total_pages,
        "completed_page_count": total_pages - page_failure_count,
        "failed_page_count": page_failure_count,
        "model_provider": "OpenAI",
        "endpoint": "/v1/responses",
        "provider_response_storage": False,
        "model": (config or PipelineConfig()).model.name,
        "reasoning_effort": (config or PipelineConfig()).model.reasoning_effort,
        "stages": (config or PipelineConfig())
        .stages.model_copy(
            update=({} if retry_failed_fields is None else {"repair": retry_failed_fields})
        )
        .model_dump(),
        "usage": {
            "input_tokens": usage.input_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "output_tokens": usage.output_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
        },
        "estimated_cost_usd": str(cost),
        "api_call_count": api_call_count,
        "routing_call_count": routing_call_count,
        "retry_count": retry_count,
        "files": [
            {
                "item_id": result.item_id,
                "source_filename": result.source_filename,
                "selected_pages": list(result.selected_pages),
                "status": result.status,
                "review_required": (
                    True if result.run is None else bool(result.run.manifest["review_required"])
                ),
                "review_state": (
                    "failed" if result.run is None else result.run.manifest["review_state"]
                ),
                "failure_reason": result.failure_reason,
                "usage": (
                    {
                        "input_tokens": result.usage.input_tokens,
                        "cached_input_tokens": result.usage.cached_input_tokens,
                        "cache_write_tokens": result.usage.cache_write_tokens,
                        "output_tokens": result.usage.output_tokens,
                        "reasoning_tokens": result.usage.reasoning_tokens,
                    }
                ),
                "estimated_cost_usd": str(result.cost_usd),
                "api_call_count": result.api_call_count,
                "routing_call_count": result.routing_call_count,
                "retry_count": result.retry_count,
            }
            for result in ordered
        ],
    }
    manifest = validate_batch_manifest(manifest)
    zip_filename = "ade-batch.outputs.zip"
    return BatchExtractionRun(
        ordered,
        usage,
        cost,
        manifest,
        zip_filename,
        build_batch_output_bundle(entries=entries, manifest=manifest),
    )


def _render_pages(
    source: DocumentInput, pages: tuple[int, ...], dpi: int
) -> tuple[RenderedPage, ...]:
    return tuple(rasterize_document(source, pages, dpi))


def _safe_stem(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    value = "".join(
        character if character.isalnum() or character in "._-" else "_" for character in stem
    )
    return value or "document"
