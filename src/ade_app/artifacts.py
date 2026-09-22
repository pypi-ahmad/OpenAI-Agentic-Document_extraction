"""Local exports derived from one immutable parse result."""

from __future__ import annotations

import base64
import io
import json
import zipfile
from dataclasses import dataclass
from html import escape
from pathlib import Path

import nh3
import pymupdf
from markdown_it import MarkdownIt

from ade_app.config import ParserConfig
from ade_app.models import DocumentResult
from ade_app.raster import RenderedPage


@dataclass(frozen=True, slots=True)
class Artifacts:
    markdown: str
    json_text: str
    html: str
    annotated_pdf: bytes
    zip_bytes: bytes
    markdown_name: str
    json_name: str
    html_name: str
    pdf_name: str
    zip_name: str


def build_artifacts(
    document: DocumentResult, assets: dict[str, bytes], pages: list[RenderedPage]
) -> Artifacts:
    stem = _safe_stem(document.source_filename)
    json_text = json.dumps(document.model_dump(mode="json"), indent=2, ensure_ascii=False)
    html = _build_html(document.markdown, assets, document.source_filename)
    pdf = _annotate(document, pages)
    names = (
        f"{stem}.md",
        f"{stem}.json",
        f"{stem}.html",
        f"{stem}.annotated.pdf",
        f"{stem}.outputs.zip",
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(names[0], document.markdown)
        archive.writestr(names[1], json_text)
        archive.writestr(names[2], html)
        archive.writestr(names[3], pdf)
        for name, data in assets.items():
            archive.writestr(name, data)
    return Artifacts(document.markdown, json_text, html, pdf, buffer.getvalue(), *names)


def estimated_cost(document: DocumentResult, config: ParserConfig | None = None):
    settings = config or ParserConfig()
    usage = document.usage
    if not usage.complete:
        return None
    uncached = usage.input_tokens - usage.cached_input_tokens - usage.cache_write_tokens
    return (
        uncached * settings.input_rate
        + usage.cached_input_tokens * settings.cached_input_rate
        + usage.cache_write_tokens * settings.cache_write_rate
        + usage.output_tokens * settings.output_rate
    ) / 1_000_000


def _build_html(markdown: str, assets: dict[str, bytes], title: str) -> str:
    embedded = markdown
    for name, data in assets.items():
        embedded = embedded.replace(
            f"]({name})", f"](data:image/png;base64,{base64.b64encode(data).decode('ascii')})"
        )
    rendered = MarkdownIt("commonmark", {"html": True}).enable("table").render(embedded)
    clean = nh3.clean(
        rendered,
        tags={
            "p",
            "br",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "strong",
            "em",
            "blockquote",
            "pre",
            "code",
            "ul",
            "ol",
            "li",
            "table",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
            "img",
            "hr",
            "figure",
            "figcaption",
            "sup",
            "sub",
        },
        attributes={
            "img": {"src", "alt"},
            "td": {"rowspan", "colspan"},
            "th": {"rowspan", "colspan"},
        },
        url_schemes={"data"},
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        f"<title>{escape(title)}</title>"
        "<style>body{font:16px/1.55 system-ui;max-width:980px;margin:2rem auto;"
        "padding:0 1rem;color:#171717}img{max-width:100%}table{border-collapse:collapse}"
        "th,td{border:1px solid #999;padding:.35rem}</style>"
        f"</head><body>{clean}</body></html>"
    )


def _annotate(document: DocumentResult, pages: list[RenderedPage]) -> bytes:
    result = pymupdf.open()
    by_page = {page.page: page for page in document.pages}
    for rendered in pages:
        page = result.new_page(width=rendered.width, height=rendered.height)
        page.insert_image(page.rect, stream=rendered.png)
        parsed = by_page[rendered.page]
        for block in parsed.blocks:
            box = block.box
            rect = pymupdf.Rect(
                box.xmin * rendered.width,
                box.ymin * rendered.height,
                box.xmax * rendered.width,
                box.ymax * rendered.height,
            )
            page.draw_rect(rect, color=(0.76, 0.09, 0.36), width=2)
            page.insert_text(
                (rect.x0 + 2, max(10, rect.y0 + 10)),
                f"{block.id} · {block.type}",
                fontsize=8,
                color=(0.76, 0.09, 0.36),
            )
    data = result.tobytes(garbage=4, deflate=True)
    result.close()
    return data


def _safe_stem(filename: str) -> str:
    value = "".join(
        char if char.isalnum() or char in "-_" else "-" for char in Path(filename).stem
    ).strip("-")
    return value or "document"
