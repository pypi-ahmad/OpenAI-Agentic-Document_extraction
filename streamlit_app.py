"""Streamlit UI for GPT-6 Sol scanned-document parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256

import streamlit as st

from ade_app.artifacts import Artifacts, build_artifacts, estimated_cost
from ade_app.inputs import DocumentInput
from ade_app.models import DocumentResult
from ade_app.parser import parse_document, resolve_api_key
from ade_app.raster import page_count
from ade_app.security import is_loopback_address, public_extraction_error

MAX_FILES = 20
MAX_BATCH_PAGES = 100
_IMAGE_LINK = re.compile(r"!\[[^\]]*\]\(assets/[^)]+\)")
_REMOTE_IMAGE = re.compile(r"!\[([^\]]*)\]\((?:https?|data|javascript):[^)]*\)")
_REMOTE_LINK = re.compile(r"\[([^\]]+)\]\((?:https?|data|javascript):[^)]*\)")
_COPY = st.components.v2.component(
    "copy_text",
    html='<button id="copy">Copy</button>',
    js="""export default function(component) {
      const b = component.querySelector('#copy');
      b.onclick = async () => {
        await navigator.clipboard.writeText(component.data.text);
        b.textContent = 'Copied';
        setTimeout(() => b.textContent = 'Copy', 1200);
      };
    }""",
)


@dataclass(frozen=True, slots=True)
class ParsedOutput:
    document: DocumentResult
    artifacts: Artifacts
    assets: dict[str, bytes]


def _secret() -> str | None:
    try:
        return st.secrets.get("OPENAI_API_KEY")
    except (FileNotFoundError, KeyError):
        return None


def _preview(source: DocumentInput) -> None:
    st.subheader("Input preview")
    if source.suffix == ".pdf":
        st.pdf(source.data, height=650)
    else:
        st.image(source.data, caption=source.filename, width="stretch")


def _safe_rendered_markdown(markdown: str) -> str:
    """Prevent parsed content from loading remote resources in the browser."""
    markdown = _IMAGE_LINK.sub("", markdown)
    markdown = _REMOTE_IMAGE.sub(r"[image: \1]", markdown)
    return _REMOTE_LINK.sub(r"\1", markdown)


def _result(output: ParsedOutput) -> None:
    document, artifacts = output.document, output.artifacts
    st.subheader(document.source_filename)
    metrics = st.columns(5)
    metrics[0].metric("Input tokens", f"{document.usage.input_tokens:,}")
    metrics[1].metric("Cached input", f"{document.usage.cached_input_tokens:,}")
    metrics[2].metric("Output tokens", f"{document.usage.output_tokens:,}")
    metrics[3].metric("API calls", document.usage.calls)
    cost = estimated_cost(document)
    metrics[4].metric("Estimated cost", "Unavailable" if cost is None else f"${cost:.6f}")
    if document.warnings:
        st.warning(f"{len(document.warnings)} parsing warning(s); review the marked output.")
        with st.expander("Parsing warnings", expanded=True):
            for warning in document.warnings:
                st.write(f"- {warning}")
    markdown_tab, annotated_tab, html_tab, json_tab = st.tabs(
        ["Output Markdown", "Annotated", "HTML", "JSON"]
    )
    with markdown_tab:
        mode = st.segmented_control(
            "Markdown view",
            ["Rendered", "Raw"],
            default="Rendered",
            key=f"md-{artifacts.markdown_name}",
        )
        with st.container(horizontal=True, horizontal_alignment="right"):
            _COPY(
                data={"text": artifacts.markdown},
                height=38,
                key=f"copy-md-{artifacts.markdown_name}",
            )
            st.download_button(
                "Download Markdown",
                artifacts.markdown,
                file_name=artifacts.markdown_name,
                mime="text/markdown",
                icon=":material/download:",
            )
        if mode == "Raw":
            st.code(artifacts.markdown, language="markdown")
        else:
            st.markdown(_safe_rendered_markdown(artifacts.markdown))
            for name, data in output.assets.items():
                st.image(data, caption=name, width="stretch")
    with annotated_tab:
        st.download_button(
            "Download annotated PDF",
            artifacts.annotated_pdf,
            file_name=artifacts.pdf_name,
            mime="application/pdf",
            icon=":material/download:",
        )
        st.pdf(artifacts.annotated_pdf, height=800)
    with html_tab:
        st.download_button(
            "Download HTML",
            artifacts.html,
            file_name=artifacts.html_name,
            mime="text/html",
            icon=":material/download:",
        )
        st.html(artifacts.html)
    with json_tab:
        with st.container(horizontal=True, horizontal_alignment="right"):
            _COPY(
                data={"text": artifacts.json_text},
                height=38,
                key=f"copy-json-{artifacts.json_name}",
            )
            st.download_button(
                "Download JSON",
                artifacts.json_text,
                file_name=artifacts.json_name,
                mime="application/json",
                icon=":material/download:",
            )
        st.json(artifacts.json_text)
    st.download_button(
        "Download all outputs",
        artifacts.zip_bytes,
        file_name=artifacts.zip_name,
        mime="application/zip",
        icon=":material/folder_zip:",
    )


st.set_page_config(
    page_title="GPT-6 Sol document parser", page_icon=":material/document_scanner:", layout="wide"
)
if not is_loopback_address(str(st.get_option("server.address") or "")):
    st.error("For security, this application must listen on a loopback address.")
    st.stop()
st.session_state.setdefault("parse_results", {})
st.title("GPT-6 Sol document parser")
st.caption(
    "Context- and layout-aware Markdown from scanned PDFs and images. "
    "No schema or business-field extraction."
)

with st.sidebar:
    st.header("Documents")
    authorized = st.checkbox(
        "I am authorized to send these pages to OpenAI and will review the output."
    )
    uploads = st.file_uploader(
        "Scanned PDFs or images",
        type=["pdf", "png", "jpg", "jpeg", "webp", "tif", "tiff", "bmp"],
        accept_multiple_files=True,
        max_upload_size=200,
    )
    if len(uploads) > MAX_FILES:
        st.error(f"Select at most {MAX_FILES} files.")
        uploads = []
    sources: list[DocumentInput] = []
    ranges: dict[str, tuple[int, int]] = {}
    page_total = 0
    for upload in uploads:
        try:
            source = DocumentInput(upload.name, upload.getvalue())
            count = page_count(source)
            sources.append(source)
            with st.expander(f"{source.filename} · {count} page(s)", expanded=len(uploads) == 1):
                if count == 1:
                    start = end = 1
                else:
                    cols = st.columns(2)
                    key = sha256(source.data).hexdigest()[:12]
                    start = int(cols[0].number_input("Start", 1, count, 1, key=f"start-{key}"))
                    end = int(cols[1].number_input("End", 1, count, count, key=f"end-{key}"))
                if start > end:
                    st.error("Start page must not exceed end page.")
                else:
                    ranges[source.filename] = (start, end)
                    page_total += end - start + 1
        except ValueError as error:
            st.error(f"{upload.name}: {error}")
    if page_total > MAX_BATCH_PAGES:
        st.error(f"Select at most {MAX_BATCH_PAGES} pages per batch.")
    submit = st.button(
        "Parse documents",
        type="primary",
        icon=":material/document_scanner:",
        disabled=not authorized
        or not sources
        or page_total > MAX_BATCH_PAGES
        or len(ranges) != len(sources),
    )
    if st.button("Reset", icon=":material/restart_alt:"):
        st.session_state.parse_results = {}
        st.rerun()

if sources:
    preview_name = st.selectbox("Preview document", [source.filename for source in sources])
    _preview(next(source for source in sources if source.filename == preview_name))

if submit:
    try:
        api_key = resolve_api_key(_secret())
        for source in sources:
            start, end = ranges[source.filename]
            pages = tuple(range(start, end + 1))
            cache_key = sha256(source.data + repr(pages).encode()).hexdigest()
            if cache_key in st.session_state.parse_results:
                continue
            bar = st.progress(0, text=f"{source.filename}: preparing")

            def update(
                done: int,
                total: int,
                page: int,
                name: str = source.filename,
                progress_bar=bar,
            ) -> None:
                progress_bar.progress(done / total, text=f"{name}: page {page} ({done}/{total})")

            document, assets, rendered = parse_document(
                source, pages, api_key=api_key, progress=update
            )
            st.session_state.parse_results[cache_key] = ParsedOutput(
                document, build_artifacts(document, assets, rendered), assets
            )
            bar.empty()
    except Exception as error:
        st.error(public_extraction_error(error))

results: dict[str, ParsedOutput] = st.session_state.parse_results
if results:
    selected = st.selectbox(
        "View parsed document",
        list(results),
        format_func=lambda key: results[key].document.source_filename,
    )
    _result(results[selected])
