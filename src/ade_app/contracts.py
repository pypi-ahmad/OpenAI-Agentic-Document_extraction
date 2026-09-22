"""Stable public result contracts shared by orchestration and pipeline entry points.

Responsible for immutable output structures (`PageRunRecord`, `ExtractionRun`)
aggregating extracted artifacts (Markdown, JSON v3, Confidence, Annotated PDF, Manifest),
usage, and lazy ZIP bundle creation.
Must NOT execute pipeline logic, parse inputs, or write to disk.
Next: ade_app.pipeline which builds and returns ExtractionRun instances.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from ade_app.cost import TokenUsage
from ade_app.models import ExtractionDocumentV3
from ade_app.openai_client import SegmentRecord
from ade_app.outputs import AnnotationLimitation, build_output_bundle


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
    full_page_fallback: bool | None = None
    layout_issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractionRun:
    artifact: ExtractionDocumentV3
    json_text: str
    markdown_filename: str
    json_filename: str
    confidence_text: str
    confidence_filename: str
    annotated_pdf_filename: str
    zip_filename: str
    usage: TokenUsage
    cost_usd: Decimal
    pages: tuple[PageRunRecord, ...]
    annotated_pdf: bytes
    annotation_limitations: tuple[AnnotationLimitation, ...]
    manifest: dict[str, Any]
    draft_markdown: str = ""
    draft_json_text: str = ""
    draft_markdown_filename: str = "draft.md"
    draft_json_filename: str = "draft.json"

    @property
    def draft_files(self) -> dict[str, str]:
        return (
            {
                self.draft_markdown_filename: self.draft_markdown,
                self.draft_json_filename: self.draft_json_text,
            }
            if self.draft_json_text
            else {}
        )

    @property
    def zip_bytes(self) -> bytes:
        """Build individual archive only when requested."""

        return build_output_bundle(
            markdown_filename=self.markdown_filename,
            markdown=self.artifact.markdown,
            json_filename=self.json_filename,
            json_text=self.json_text,
            confidence_filename=self.confidence_filename,
            confidence_text=self.confidence_text,
            annotated_pdf_filename=self.annotated_pdf_filename,
            annotated_pdf=self.annotated_pdf,
            manifest=self.manifest,
            extra_files=self.draft_files,
        )
