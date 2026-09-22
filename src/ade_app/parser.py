"""Single-model, context-aware scanned-document parser."""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from openai import OpenAI
from pydantic import ValidationError

from ade_app.config import ParserConfig
from ade_app.inputs import DocumentInput
from ade_app.models import (
    Block,
    Box,
    CropRead,
    DocumentResult,
    PageRead,
    PageResult,
    Usage,
    ZoomRequest,
)
from ade_app.prompts import load_prompt, page_request_prompt, render_prompt
from ade_app.raster import RenderedPage, crop_page, render_pages

VISUAL_TYPES = {"figure", "chart", "diagram", "attestation", "logo", "scan_code"}
PAGE_MAX_OUTPUT_TOKENS = 128_000
MARKDOWN_MAX_OUTPUT_TOKENS = 64_000
CROP_MAX_OUTPUT_TOKENS = 32_000
STRUCTURED_READ_ATTEMPTS = 2
CROP_MARGIN = 0.02


class StructuredReadError(RuntimeError):
    def __init__(self, label: str, attempts: int) -> None:
        super().__init__(f"{label} returned incomplete structured output after {attempts} attempts")
        self.attempts = attempts


def resolve_api_key(secret: str | None = None) -> str:
    key = os.environ.get("OPENAI_API_KEY") or secret
    if not key or key != key.strip() or any(ord(char) < 32 for char in key):
        raise RuntimeError("Required credential OPENAI_API_KEY is unavailable or invalid")
    return key


def _base_url() -> str:
    configured = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    parsed = urlparse(configured)
    host = (parsed.hostname or "").lower()
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise RuntimeError("OPENAI_BASE_URL must not contain credentials or a non-HTTPS port")
    if parsed.scheme != "https" or not (
        host == "api.openai.com" or host.endswith(".api.openai.com")
    ):
        raise RuntimeError("OPENAI_BASE_URL must use an official HTTPS OpenAI endpoint")
    if parsed.path.rstrip("/") not in {"", "/v1"} or parsed.query or parsed.fragment:
        raise RuntimeError("OPENAI_BASE_URL must target the OpenAI /v1 API root")
    return (
        configured.rstrip("/")
        if parsed.path.rstrip("/") == "/v1"
        else configured.rstrip("/") + "/v1"
    )


def build_client(api_key: str, config: ParserConfig) -> Any:
    return OpenAI(
        api_key=api_key, base_url=_base_url(), timeout=config.timeout_seconds, max_retries=2
    ).responses


def parse_document(
    source: DocumentInput,
    selected_pages: tuple[int, ...],
    *,
    api_key: str,
    config: ParserConfig | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> tuple[DocumentResult, dict[str, bytes], list[RenderedPage]]:
    settings = config or ParserConfig()
    rendered = render_pages(source, selected_pages, settings.dpi)
    responses = build_client(api_key, settings)
    results: list[PageResult] = []
    assets: dict[str, bytes] = {}
    total_usage = Usage()
    for index, page in enumerate(rendered):
        before = rendered[index - 1] if index else None
        after = rendered[index + 1] if index + 1 < len(rendered) else None
        try:
            result = _parse_page(responses, page, before, after, len(rendered), settings)
        except StructuredReadError as exc:
            result = PageResult(
                page=page.page,
                width=page.width,
                height=page.height,
                blocks=[],
                warnings=[
                    f"Page {page.page} failed: incomplete structured model output after "
                    f"{exc.attempts} attempts. Token usage for failed attempts is unavailable."
                ],
                status="failed",
                usage=Usage(calls=exc.attempts, complete=False),
            )
        for block in result.blocks:
            if block.type in VISUAL_TYPES:
                name = f"assets/page-{page.page}-{block.id}.png"
                assets[name] = crop_page(page, block.box)
                block.asset = name
        results.append(result)
        total_usage = total_usage.plus(result.usage)
        if progress:
            progress(index + 1, len(rendered), page.page)
    markdown = _render_markdown(results)
    document = DocumentResult(
        source_filename=source.filename,
        pages=results,
        markdown=markdown,
        warnings=[warning for page in results for warning in page.warnings],
        usage=total_usage,
    )
    return document, assets, rendered


def _parse_page(
    responses: Any,
    page: RenderedPage,
    before: RenderedPage | None,
    after: RenderedPage | None,
    page_count: int,
    config: ParserConfig,
) -> PageResult:
    request = page_request_prompt(
        has_previous=before is not None,
        has_next=after is not None,
        target_page=page.page,
        selected_page_count=page_count,
    )
    content: list[dict[str, str]] = [{"type": "input_text", "text": request}]
    if before:
        content.append(_image(before.png))
    content.append(_image(page.png))
    if after:
        content.append(_image(after.png))
    try:
        response, failed_attempts = _parse_structured(
            responses,
            label=f"page {page.page}",
            attempts=1,
            model=config.model,
            instructions=load_prompt("page_parse.md"),
            input=[{"role": "user", "content": content}],
            text_format=PageRead,
            reasoning={"effort": config.reasoning_effort},
            max_output_tokens=PAGE_MAX_OUTPUT_TOKENS,
            store=False,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError(f"page {page.page} did not return a parsed result")
        usage = _usage(response).plus(Usage(calls=failed_attempts))
        fallback_warnings: list[str] = []
    except StructuredReadError as full_page_error:
        try:
            parsed, usage = _read_page_markdown(responses, page, config)
        except StructuredReadError as markdown_error:
            raise StructuredReadError(
                f"page {page.page}", full_page_error.attempts + markdown_error.attempts
            ) from markdown_error
        usage = usage.plus(Usage(calls=full_page_error.attempts, complete=False))
        failed_attempts = full_page_error.attempts
        fallback_warnings = [
            "Structured output was incomplete; the page was recovered as layout-aware Markdown "
            "with one full-page grounding box. Fine-grained block boxes are unavailable, and "
            "token usage for the failed structured attempt is unavailable."
        ]
    blocks = {block.id: block for block in parsed.blocks}
    warnings = [*fallback_warnings, *parsed.warnings]
    if failed_attempts and not fallback_warnings:
        warnings.append(
            f"Page read succeeded after {failed_attempts} incomplete structured response; "
            "token usage for the failed attempt is unavailable."
        )
    requests = parsed.zoom_requests[: config.max_crops_per_page]
    crop_count = 0
    for _round in range(config.max_zoom_rounds):
        if not requests or crop_count >= config.max_crops_per_page:
            break
        next_requests: list[ZoomRequest] = []
        for request in requests[: config.max_crops_per_page - crop_count]:
            crop_count += 1
            try:
                result, crop_usage, failed_attempts = _read_crop(responses, page, request, config)
            except StructuredReadError as exc:
                usage = usage.plus(Usage(calls=exc.attempts, complete=False))
                warnings.append(
                    f"Crop reread failed after {exc.attempts} incomplete structured responses; "
                    "the full-page reading was retained and failed-attempt token usage is "
                    "unavailable."
                )
                continue
            usage = usage.plus(crop_usage)
            if failed_attempts:
                warnings.append(
                    f"Crop reread succeeded after {failed_attempts} incomplete structured "
                    "response; failed-attempt token usage is unavailable."
                )
            blocks.update((block.id, block) for block in result.replacement_blocks)
            warnings.extend(result.warnings)
            next_requests.extend(result.needs_another_zoom)
        requests = next_requests
    if requests:
        warnings.append(
            "Adaptive reread limit reached; remaining small or ambiguous text may need review."
        )
    return PageResult(
        page=page.page,
        width=page.width,
        height=page.height,
        blocks=list(blocks.values()),
        warnings=warnings,
        status="partial" if warnings else "ok",
        usage=usage,
    )


def _read_crop(
    responses: Any, page: RenderedPage, request: ZoomRequest, config: ParserConfig
) -> tuple[CropRead, Usage, int]:
    crop_box = _expanded_box(request.box)
    prompt = render_prompt(
        "crop_request.md",
        source_page=page.page,
        crop_box=crop_box.model_dump_json(),
        rotation_degrees=request.rotate_degrees,
        reason=request.reason,
    )
    response, failed_attempts = _parse_structured(
        responses,
        label=f"page {page.page} crop",
        model=config.model,
        instructions=load_prompt("crop_read.md"),
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    _image(crop_page(page, crop_box, request.rotate_degrees)),
                ],
            }
        ],
        text_format=CropRead,
        reasoning={"effort": config.reasoning_effort},
        max_output_tokens=CROP_MAX_OUTPUT_TOKENS,
        store=False,
    )
    if response.output_parsed is None:
        raise RuntimeError(f"page {page.page} crop did not return a parsed result")
    return (
        response.output_parsed,
        _usage(response).plus(Usage(calls=failed_attempts, complete=not failed_attempts)),
        failed_attempts,
    )


def _read_page_markdown(
    responses: Any, page: RenderedPage, config: ParserConfig
) -> tuple[PageRead, Usage]:
    response = responses.create(
        model=config.model,
        instructions=load_prompt("page_markdown.md"),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": render_prompt("page_markdown_request.md", source_page=page.page),
                    },
                    _image(page.png),
                ],
            }
        ],
        reasoning={"effort": config.reasoning_effort},
        max_output_tokens=MARKDOWN_MAX_OUTPUT_TOKENS,
        store=False,
    )
    markdown = str(getattr(response, "output_text", "") or "").strip()
    if not markdown:
        raise StructuredReadError(f"page {page.page} Markdown fallback", 1)
    return (
        PageRead(
            blocks=[
                Block(
                    id="paragraph-0",
                    type="paragraph",
                    markdown=markdown,
                    box=Box(xmin=0, ymin=0, xmax=1, ymax=1),
                )
            ]
        ),
        _usage(response),
    )


def _parse_structured(
    responses: Any,
    *,
    label: str,
    attempts: int = STRUCTURED_READ_ATTEMPTS,
    **kwargs: Any,
) -> tuple[Any, int]:
    for attempt in range(attempts):
        try:
            return responses.parse(**kwargs), attempt
        except ValidationError as exc:
            if attempt + 1 == attempts:
                raise StructuredReadError(label, attempts) from exc
    raise AssertionError("structured read loop must return or raise")


def _expanded_box(box: Box) -> Box:
    return Box(
        xmin=max(0.0, box.xmin - CROP_MARGIN),
        ymin=max(0.0, box.ymin - CROP_MARGIN),
        xmax=min(1.0, box.xmax + CROP_MARGIN),
        ymax=min(1.0, box.ymax + CROP_MARGIN),
    )


def _render_markdown(pages: list[PageResult]) -> str:
    output: list[str] = []
    for page in pages:
        for block in page.blocks:
            output.append(block.markdown.strip())
            if block.asset:
                output.append(f"![{block.type} {block.id}]({block.asset})")
            if block.description:
                output.append(f"*Generated description: {block.description}*")
        if page != pages[-1]:
            output.append("<!-- PAGE BREAK -->")
    return "\n\n".join(output).strip() + "\n"


def _usage(response: Any) -> Usage:
    value = getattr(response, "usage", None)
    if value is None:
        return Usage(calls=1, complete=False)
    input_details = getattr(value, "input_tokens_details", None)
    output_details = getattr(value, "output_tokens_details", None)
    return Usage(
        input_tokens=int(getattr(value, "input_tokens", 0) or 0),
        cached_input_tokens=int(getattr(input_details, "cached_tokens", 0) or 0),
        cache_write_tokens=int(getattr(input_details, "cache_write_tokens", 0) or 0),
        output_tokens=int(getattr(value, "output_tokens", 0) or 0),
        reasoning_tokens=int(getattr(output_details, "reasoning_tokens", 0) or 0),
        calls=1,
    )


def _image(data: bytes) -> dict[str, str]:
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "type": "input_image",
        "image_url": f"data:image/png;base64,{encoded}",
        "detail": "original",
    }
