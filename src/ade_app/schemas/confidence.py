"""Strict confidence-report schema kept separate from parse JSON v2.

Must not gain fields the writer side (services/confidence.py) doesn't populate,
and must not be relaxed to accept values the report builder never produces.
Continue to services/confidence.py to see how a ConfidenceReport is built.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ade_app.models import StrictModel


class PageConfidence(StrictModel):
    source_page: int = Field(ge=1)
    status: Literal["ok", "failed"]
    confidence: float | None = Field(ge=0, le=1)
    models_used: list[str]
    review_reasons: list[str]


class FieldConfidence(StrictModel):
    field_id: str
    canonical_name: str
    confidence: float | None = Field(ge=0, le=1)
    status: Literal["accepted", "needs_review", "conflict"]
    review_reasons: list[str]
    selected_value: str | bool | None
    evidence_pages: list[int]
    evidence_routes: list[Literal["local_text", "local_table", "luna", "terra", "sol"]]
    validation_checks: list[str]


class ReviewItem(StrictModel):
    priority: int = Field(ge=1)
    field_id: str
    canonical_name: str
    status: Literal["needs_review", "conflict"]
    confidence: float | None = Field(ge=0, le=1)
    reasons: list[str]


class ConfidenceReport(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    document_confidence: float | None = Field(ge=0, le=1)
    review_required: bool
    pages: list[PageConfidence]
    fields: list[FieldConfidence]
    accepted_field_count: int = Field(ge=0)
    review_field_count: int = Field(ge=0)
    failed_page_count: int = Field(ge=0)
    review_queue: list[ReviewItem]


# Legacy* models mirror the 2.0 shapes above but pin schema_version "1.1" and require
# non-null confidence/value fields. They exist only so parse_confidence_report can still
# validate confidence reports written before "unknown confidence" (None) was introduced;
# nothing in this codebase writes Legacy* reports anymore.
class LegacyPageConfidence(StrictModel):
    source_page: int = Field(ge=1)
    status: Literal["ok", "failed"]
    confidence: float = Field(ge=0, le=1)
    models_used: list[str]
    review_reasons: list[str]


class LegacyFieldConfidence(StrictModel):
    field_id: str
    canonical_name: str
    confidence: float = Field(ge=0, le=1)
    status: Literal["accepted", "needs_review", "conflict"]
    review_reasons: list[str]
    selected_value: str | bool
    evidence_pages: list[int]
    evidence_routes: list[Literal["local_text", "local_table", "luna", "terra", "sol"]]
    validation_checks: list[str]


class LegacyReviewItem(StrictModel):
    priority: int = Field(ge=1)
    field_id: str
    canonical_name: str
    status: Literal["needs_review", "conflict"]
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]


class LegacyConfidenceReport(StrictModel):
    schema_version: Literal["1.1"] = "1.1"
    document_confidence: float = Field(ge=0, le=1)
    review_required: bool
    pages: list[LegacyPageConfidence]
    fields: list[LegacyFieldConfidence]
    accepted_field_count: int = Field(ge=0)
    review_field_count: int = Field(ge=0)
    failed_page_count: int = Field(ge=0)
    review_queue: list[LegacyReviewItem]


def parse_confidence_report(data: str | bytes) -> ConfidenceReport | LegacyConfidenceReport:
    """Dispatch on the on-disk schema_version so older 1.1 reports still parse."""

    import json

    payload = json.loads(data)
    version = payload.get("schema_version") if isinstance(payload, dict) else None
    if version == "1.1":
        return LegacyConfidenceReport.model_validate(payload)
    if version == "2.0":
        return ConfidenceReport.model_validate(payload)
    raise ValueError("unsupported confidence schema version")
