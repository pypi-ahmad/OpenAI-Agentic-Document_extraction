"""Streamlit UI entry point: authorization gate, upload/page-range form, and result display.

Responsible for collecting and validating operator input (authorization
acknowledgement, uploaded files, inclusive page ranges) and rendering batch
results/downloads. Must NOT perform validation, rasterization, or extraction
itself, and must never log or display the OpenAI credential. Delegates to
ade_app.inputs (page-range/byte validation), ade_app.raster (rendering), and
ade_app.batch (bounded concurrent orchestration) — open ade_app/batch.py next
to follow how a submitted batch is executed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import streamlit as st
from streamlit.typing import UploadedFile

from ade_app.batch import (
    MAX_BATCH_BYTES,
    MAX_BATCH_FILES,
    MAX_BATCH_PAGES,
    MAX_FILE_WORKERS,
    MAX_PAGE_WORKERS_PER_DOCUMENT,
    BatchDocument,
    BatchExtractionRun,
    BatchProgress,
    extract_documents,
)
from ade_app.config import PipelineConfig
from ade_app.constants import DEFAULT_DPI, MODEL_CASCADE, MODEL_ID
from ade_app.hybrid import HybridPageExtractor, routing_fingerprint
from ade_app.inputs import DocumentInput, parse_page_range
from ade_app.pipeline import ExtractionRun
from ade_app.preview import safe_markdown_preview
from ade_app.raster import RenderedPage, get_page_count, rasterize_document
from ade_app.runner import create_extractor
from ade_app.security import is_loopback_address, public_extraction_error
from ade_app.session import clear_page_range_state, reset_session_state


@dataclass(frozen=True, slots=True)
class _UploadItem:
    item_id: str
    source: DocumentInput
    page_count: int


_COPY_BUTTON = st.components.v2.component(
    "output_copy_button",
    html='<button type="button" aria-label="Copy output">Copy</button>',
    css="""
button {
  color: var(--st-text-color);
  background: transparent;
  border: 1px solid var(--st-border-color);
  border-radius: var(--st-button-border-radius);
  padding: 0.35rem 0.75rem;
  cursor: pointer;
}
button:hover { border-color: var(--st-primary-color); }
""",
    js="""
export default function(component) {
  const { data, parentElement } = component;
  const button = parentElement.querySelector("button");
  button.onclick = async () => {
    await navigator.clipboard.writeText(data.text);
    button.textContent = "Copied";
    setTimeout(() => { button.textContent = "Copy"; }, 1500);
  };
}
""",
)


def _document_input(upload: UploadedFile) -> DocumentInput:
    return DocumentInput(filename=upload.name, data=upload.getvalue())


def _upload_item(index: int, upload: UploadedFile) -> _UploadItem:
    # item_id combines upload order with a content digest so two files with
    # identical bytes (or a rename) still get distinct, stable widget keys.
    source = _document_input(upload)
    digest = sha256(source.filename.encode("utf-8") + b"\0" + source.data).hexdigest()[:16]
    return _UploadItem(f"{index}-{digest}", source, _page_count(source.filename, source.data))


def _streamlit_api_key() -> str | None:
    # Credential trust boundary: prefer the environment variable; fall back to
    # secrets.toml only if unset. The key is returned for immediate use by the
    # extractor and must never be logged, displayed, or included in an artifact.
    if os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        value = st.secrets.get("OPENAI_API_KEY")
    except FileNotFoundError:
        return None
    return str(value) if value else None


def _document_extractor(api_key: str | None, config: PipelineConfig) -> HybridPageExtractor:
    """Keep document-bearing extractor state within the current extraction run."""

    return create_extractor(config, api_key=api_key)


def _raster_pages(
    filename: str, data: bytes, pages: tuple[int, ...], dpi: int
) -> tuple[RenderedPage, ...]:
    return tuple(rasterize_document(DocumentInput(filename=filename, data=data), pages, dpi))


def _page_count(filename: str, data: bytes) -> int:
    return get_page_count(DocumentInput(filename=filename, data=data))


def _reset() -> None:
    reset_session_state(st.session_state)


def _render_document_result(run_result: ExtractionRun) -> None:
    st.caption(
        f"Model: gpt-6-sol · Reasoning: medium · Stages: {run_result.manifest.get('stages', {})}"
    )
    output_view = st.segmented_control("Output", ["Draft", "Verified"], default="Draft")
    is_draft = output_view != "Verified" and bool(run_result.draft_json_text)
    shown_markdown = run_result.draft_markdown if is_draft else run_result.artifact.markdown
    shown_json = run_result.draft_json_text if is_draft else run_result.json_text
    if is_draft:
        st.info("Unverified primary extraction. Review draft values against the source document.")
    failed_pages = [page.source_page for page in run_result.pages if page.status == "failed"]
    if failed_pages:
        st.warning(
            f"Partial result: {len(failed_pages)} page(s) failed: "
            f"{', '.join(map(str, failed_pages))}. Successful pages remain downloadable."
        )
    review_state = str(run_result.manifest["review_state"])
    if review_state != "not_required":
        st.warning(
            "These downloads contain unresolved or failed extraction results. "
            "Treat them as unreviewed artifacts."
        )
    metric_columns = st.columns(5)
    metric_columns[0].metric("Input tokens", f"{run_result.usage.input_tokens:,}")
    metric_columns[1].metric("Cached input", f"{run_result.usage.cached_input_tokens:,}")
    metric_columns[2].metric("Cache write", f"{run_result.usage.cache_write_tokens:,}")
    metric_columns[3].metric("Output tokens", f"{run_result.usage.output_tokens:,}")
    metric_columns[4].metric("Estimated cost", f"${run_result.cost_usd:.6f}")
    with st.container(horizontal=True):
        st.download_button(
            "Draft Markdown" if is_draft else "Verified Markdown",
            shown_markdown,
            file_name=run_result.draft_markdown_filename
            if is_draft
            else run_result.markdown_filename,
            mime="text/markdown",
            icon=":material/download:",
        )
        st.download_button(
            "Draft JSON" if is_draft else "Verified JSON",
            shown_json,
            file_name=run_result.draft_json_filename if is_draft else run_result.json_filename,
            mime="application/json",
            icon=":material/download:",
        )
        st.download_button(
            "Confidence",
            run_result.confidence_text,
            file_name=run_result.confidence_filename,
            mime="application/json",
            icon=":material/download:",
        )
        st.download_button(
            "Annotated PDF",
            run_result.annotated_pdf,
            file_name=run_result.annotated_pdf_filename,
            mime="application/pdf",
            icon=":material/download:",
        )
        st.download_button(
            "All outputs",
            run_result.zip_bytes,
            file_name=run_result.zip_filename,
            mime="application/zip",
            icon=":material/folder_zip:",
        )

    markdown_tab, json_tab, pdf_tab, usage_tab = st.tabs(
        ["Markdown", "JSON", "Annotated PDF", "Usage"]
    )
    with markdown_tab:
        with st.container(horizontal=True, horizontal_alignment="right"):
            _COPY_BUTTON(data={"text": shown_markdown}, height=38)
        st.markdown(
            safe_markdown_preview(shown_markdown),
            unsafe_allow_html=True,
        )
    with json_tab:
        with st.container(horizontal=True, horizontal_alignment="right"):
            _COPY_BUTTON(data={"text": shown_json}, height=38)
        st.json(shown_json)
    with pdf_tab:
        st.pdf(run_result.annotated_pdf, height=800)
        for limitation in run_result.annotation_limitations:
            label = f" · {limitation.element_id}" if limitation.element_id else ""
            st.caption(f"Page {limitation.source_page}{label}: {limitation.reason}")
    with usage_tab:
        needs_review = [
            (page.source_page, segment)
            for page in run_result.pages
            for segment in page.segments
            if segment.status == "needs_review"
        ]
        if needs_review:
            st.warning(f"{len(needs_review)} segment(s) need human review.")
            st.dataframe(
                [
                    {
                        "page": page,
                        "segment": segment.segment_id,
                        "score": segment.final_score,
                        "reasons": ", ".join(segment.reasons),
                        "models": " → ".join(attempt.model for attempt in segment.attempts),
                    }
                    for page, segment in needs_review
                ],
                hide_index=True,
            )
        st.subheader("Pages")
        st.dataframe(
            [
                {
                    "page": page.source_page,
                    "status": page.status,
                    "input": page.usage.input_tokens,
                    "cached input": page.usage.cached_input_tokens,
                    "cache write": page.usage.cache_write_tokens,
                    "output": page.usage.output_tokens,
                    "estimated USD": float(page.cost_usd),
                    "elapsed ms": page.elapsed_ms,
                    "range repairs": page.range_repairs,
                    "failure": page.failure_reason,
                }
                for page in run_result.pages
            ],
            hide_index=True,
        )
        st.subheader("Models")
        model_rows = []
        for model, _ in MODEL_CASCADE:
            usages = [
                usage
                for page in run_result.pages
                for used_model, usage in page.usage_by_model
                if used_model == model
            ]
            if usages:
                model_rows.append(
                    {
                        "model": model,
                        "input": sum(usage.input_tokens for usage in usages),
                        "cached input": sum(usage.cached_input_tokens for usage in usages),
                        "cache write": sum(usage.cache_write_tokens for usage in usages),
                        "output": sum(usage.output_tokens for usage in usages),
                    }
                )
        st.dataframe(model_rows, hide_index=True)


st.set_page_config(
    page_title="OpenAI document extraction",
    page_icon=":material/document_scanner:",
    layout="wide",
)
# Defense-in-depth: scripts/launch.ps1 is expected to bind Streamlit to
# 127.0.0.1, but this check catches a manual `streamlit run` that skips the
# launcher and would otherwise expose the app beyond localhost.
if not is_loopback_address(str(st.get_option("server.address") or "")):
    st.error("For security, this application must listen on a loopback address.")
    st.stop()
st.session_state.setdefault("batch_run", None)

st.title("OpenAI agentic document extraction")
st.caption(
    f"Scanned-document extraction using only `{MODEL_ID}`. "
    "Produces structured JSON, Markdown, an annotated PDF, and a confidence report."
)
upload_items: list[_UploadItem] = []
page_ranges: dict[str, tuple[int, int]] = {}
submitted = False
batch_error = False
with st.sidebar:
    st.header("Documents")
    st.caption(
        "Selected pages and routed review crops are sent to OpenAI. Results remain in server-side "
        "state until Reset, the upload selection changes, or this Streamlit session ends. Upload "
        "only documents you are authorized to process."
    )
    authorized = st.checkbox(
        "I am authorized to send these pages to OpenAI and will review flagged output.",
        key="authorization_acknowledged",
    )
    st.caption("Extraction is always enabled. Additional stages apply to the next submitted run.")
    verify_enabled = st.toggle("Verify extraction", key="verification_enabled", value=False)
    repair_enabled = st.toggle(
        "Repair flagged fields",
        key="repair_enabled",
        value=False,
        help="Reread up to eight flagged fields. Can run without segment verification.",
    )
    run_config = PipelineConfig.model_validate(
        {"stages": {"verification": verify_enabled, "repair": repair_enabled}}
    )
    uploaded_files = st.file_uploader(
        "Scanned PDFs or images",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
        max_upload_size=200,
        key="document_upload",
        help=f"Upload up to {MAX_BATCH_FILES} files. Each file may be up to 200 MB.",
    )
    if len(uploaded_files) > MAX_BATCH_FILES:
        st.error(f"Select at most {MAX_BATCH_FILES} files; {len(uploaded_files)} selected.")
        batch_error = True
    else:
        for index, upload in enumerate(uploaded_files, 1):
            try:
                upload_items.append(_upload_item(index, upload))
            except ValueError as error:
                st.error(f"{upload.name}: {error}")
                batch_error = True
        if sum(len(item.source.data) for item in upload_items) > MAX_BATCH_BYTES:
            st.error("Total upload size may not exceed 500 MB.")
            batch_error = True

    # The upload selection is the source of truth: whenever it changes (files
    # added/removed/reordered), drop any stale per-file range widget state and
    # any previous batch result rather than letting them silently carry over.
    fingerprint = "|".join(item.item_id for item in upload_items)
    if st.session_state.get("upload_fingerprint") != fingerprint:
        clear_page_range_state(st.session_state)
        st.session_state.batch_run = None
        st.session_state.pop("result_item_id", None)
        st.session_state.upload_fingerprint = fingerprint

    if upload_items and not batch_error:
        with st.form("extract_documents", border=True):
            st.subheader("Page ranges")
            for item in upload_items:
                suffix = "s" if item.page_count != 1 else ""
                with st.expander(
                    f"{item.source.filename} · {item.page_count} page{suffix}",
                    expanded=len(upload_items) == 1,
                ):
                    start_key = f"start_page_{item.item_id}"
                    end_key = f"end_page_{item.item_id}"
                    st.session_state.setdefault(start_key, 1)
                    st.session_state.setdefault(end_key, item.page_count)
                    columns = st.columns(2)
                    start_page = int(
                        columns[0].number_input(
                            "Start page",
                            min_value=1,
                            max_value=item.page_count,
                            step=1,
                            key=start_key,
                            disabled=item.page_count == 1,
                        )
                    )
                    end_page = int(
                        columns[1].number_input(
                            "End page",
                            min_value=1,
                            max_value=item.page_count,
                            step=1,
                            key=end_key,
                            disabled=item.page_count == 1,
                        )
                    )
                    page_ranges[item.item_id] = (start_page, end_page)
                    # Both bounds are 1-based and inclusive; equal start/end selects
                    # a single page. Rejected here before ade_app.inputs.parse_page_range
                    # ever sees the "{start}-{end}" string built from this pair.
                    if start_page > end_page:
                        st.error("Start page must be less than or equal to end page.")
                        batch_error = True
            selected_page_count = sum(
                end - start + 1 for start, end in page_ranges.values() if start <= end
            )
            if selected_page_count > MAX_BATCH_PAGES:
                st.error(f"Select at most {MAX_BATCH_PAGES} pages per batch.")
                batch_error = True
            st.caption(
                f"{len(upload_items)} file(s) · {selected_page_count} selected page(s) · "
                f"up to {min(len(upload_items), MAX_FILE_WORKERS)} concurrent documents, "
                f"{MAX_PAGE_WORKERS_PER_DOCUMENT} pages per document"
            )
            submitted = st.form_submit_button(
                "Extract documents",
                type="primary",
                icon=":material/document_scanner:",
                disabled=batch_error or not authorized,
                help=None if authorized else "Confirm authorization and review responsibility.",
            )
    else:
        st.info(f"Upload 1-{MAX_BATCH_FILES} documents to choose their page ranges.")

    st.button("Reset", icon=":material/restart_alt:", on_click=_reset)

if submitted and upload_items:
    st.session_state.batch_run = None
    try:
        documents = tuple(
            BatchDocument(
                item.item_id,
                item.source,
                parse_page_range(
                    f"{page_ranges[item.item_id][0]}-{page_ranges[item.item_id][1]}",
                    item.page_count,
                ),
            )
            for item in upload_items
        )
        # Cache key covers everything that can change the extraction result:
        # exact document bytes/pages, routing/config fingerprint, on-disk
        # calibration profiles, and the retry toggle. Re-submitting the same
        # form without changing any of these replays the cached run instead
        # of re-billing the OpenAI API.
        cache_key = sha256(
            json.dumps(
                {
                    "documents": [
                        (doc.source.filename, sha256(doc.source.data).hexdigest(), doc.pages)
                        for doc in documents
                    ],
                    "routing": routing_fingerprint(run_config),
                    "profiles": [
                        (str(path), sha256(path.read_bytes()).hexdigest())
                        for path in sorted(Path("profiles").glob("*.json"))
                    ],
                    "stages": run_config.stages.model_dump(),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        cached = st.session_state.get("extraction_cache")
        if cached is not None and cached[0] == cache_key:
            st.session_state.batch_run = cached[1]
            st.rerun()
        extractor = _document_extractor(_streamlit_api_key(), run_config)
        total_pages = sum(len(document.pages) for document in documents)
        progress_bar = st.progress(0, text=f"0% · 0 completed · 0 failed · {total_pages} total")
        with st.status("Processing documents", expanded=True) as extraction_status:

            def update_progress(event: BatchProgress) -> None:
                percent = round(event.completed_pages * 100 / event.total_pages)
                successful = event.completed_pages - event.failed_pages
                progress_bar.progress(
                    event.completed_pages / event.total_pages,
                    text=(
                        f"{percent}% · {successful} completed · {event.failed_pages} failed · "
                        f"{event.total_pages} total"
                    ),
                )
                if event.source_page is None:
                    extraction_status.write(
                        f"{event.filename}: {event.status} "
                        f"({event.completed_files}/{event.total_files} files)"
                    )
                else:
                    extraction_status.write(
                        f"{event.filename} · page {event.source_page}: {event.status}"
                    )

            batch_run = extract_documents(
                documents,
                extractor,
                dpi=DEFAULT_DPI,
                max_file_workers=MAX_FILE_WORKERS,
                render_pages=lambda source, pages, dpi: _raster_pages(
                    source.filename, source.data, pages, dpi
                ),
                progress=update_progress,
                config=run_config,
            )
            extraction_status.update(
                label="Batch extraction complete", state="complete", expanded=False
            )
        st.session_state.batch_run = batch_run
        if all(item.run is not None and item.status != "failed" for item in batch_run.files):
            st.session_state.extraction_cache = (cache_key, batch_run)
    except (RuntimeError, ValueError) as error:
        # public_extraction_error strips provider/path details from the
        # message before it reaches the UI; do not surface `error` directly.
        st.error(public_extraction_error(error))

batch_result: BatchExtractionRun | None = st.session_state.batch_run
if batch_result is not None:
    downloadable_results = [result for result in batch_result.files if result.run is not None]
    successful_results = [result for result in batch_result.files if result.status == "ok"]
    partial_results = [result for result in batch_result.files if result.status == "partial"]
    failed_results = [result for result in batch_result.files if result.run is None]
    if partial_results or failed_results:
        st.warning(
            f"{len(successful_results)} file(s) completed; "
            f"{len(partial_results)} partial; {len(failed_results)} failed."
        )
    else:
        st.success(f"All {len(successful_results)} file(s) completed.")
    review_required_count = sum(
        result.run is None or bool(result.run.manifest["review_required"])
        for result in batch_result.files
    )
    if review_required_count:
        st.warning(
            f"Human review required for {review_required_count} file(s) before downstream use."
        )
    metric_columns = st.columns(5)
    metric_columns[0].metric("Input tokens", f"{batch_result.usage.input_tokens:,}")
    metric_columns[1].metric("Cached input", f"{batch_result.usage.cached_input_tokens:,}")
    metric_columns[2].metric("Cache write", f"{batch_result.usage.cache_write_tokens:,}")
    metric_columns[3].metric("Output tokens", f"{batch_result.usage.output_tokens:,}")
    metric_columns[4].metric("Estimated cost", f"${batch_result.cost_usd:.6f}")
    st.download_button(
        "Download batch outputs",
        batch_result.zip_bytes,
        file_name=batch_result.zip_filename,
        mime="application/zip",
        icon=":material/folder_zip:",
    )
    st.dataframe(
        [
            {
                "file": result.source_filename,
                "pages": f"{result.selected_pages[0]}-{result.selected_pages[-1]}",
                "status": result.status,
                "review required": (
                    True if result.run is None else bool(result.run.manifest["review_required"])
                ),
                "input": result.usage.input_tokens,
                "cached input": result.usage.cached_input_tokens,
                "cache write": result.usage.cache_write_tokens,
                "output": result.usage.output_tokens,
                "estimated USD": float(result.cost_usd),
                "failure": result.failure_reason,
            }
            for result in batch_result.files
        ],
        hide_index=True,
    )

    if downloadable_results:
        selected_item_id = st.selectbox(
            "View document",
            [result.item_id for result in downloadable_results],
            format_func=lambda item_id: next(
                result.source_filename
                for result in downloadable_results
                if result.item_id == item_id
            ),
            key="result_item_id",
        )
        selected_result = next(
            result for result in downloadable_results if result.item_id == selected_item_id
        )
        if selected_result.run is None:
            st.error("The selected document has no downloadable result.")
        else:
            _render_document_result(selected_result.run)
