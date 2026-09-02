"""Deterministic, content-safe provenance helpers for extraction manifests."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ade_app.constants import DEFAULT_DPI, QUALITY_PROFILE_PATH
from ade_app.inputs import DocumentInput
from ade_app.raster import RenderedPage

MANIFEST_SCHEMA_VERSION = 6
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ReviewState = Literal["not_required", "required_unresolved", "failed"]
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_PATHS = (
    _PROJECT_ROOT / "docs/governance/system-card.md",
    _PROJECT_ROOT / "docs/governance/acceptable-use.md",
    _PROJECT_ROOT / "docs/governance/validation-change-control.md",
)


class StrictManifestModel(BaseModel):
    """Manifest schema base that rejects unknown fields and type coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


class GovernanceManifestFields(StrictManifestModel):
    manifest_schema_version: Literal[6]
    governance_policy_version: str
    intended_use: str
    data_classification: str
    sensitive_metadata_warning: str
    canonical_markdown_trusted: bool
    evaluation_artifacts_sensitive: bool
    governance_policy_sha256: dict[str, Sha256 | None]


class ApplicationManifest(StrictManifestModel):
    name: Literal["ade-app"]
    version: str


class RenderedPageManifest(StrictManifestModel):
    source_page: int = Field(ge=1)
    sha256: Sha256
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class QualityProfileManifest(StrictManifestModel):
    path: str
    sha256: Sha256 | None
    routing_mode: Literal["repair_all", "quality_gated"]


class DocumentProvenanceFields(GovernanceManifestFields):
    generated_at_utc: str
    application: ApplicationManifest
    source_sha256: Sha256
    raster_dpi: int = Field(gt=0)
    rendered_pages: list[RenderedPageManifest]
    quality_profile: QualityProfileManifest


class ModelRouteManifest(StrictManifestModel):
    model: str
    reasoning_effort: Literal["low", "medium", "high"]


class TokenUsageManifest(StrictManifestModel):
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_input_breakdown(self) -> Self:
        if self.cached_input_tokens + self.cache_write_tokens > self.input_tokens:
            raise ValueError("cached and cache-write tokens cannot exceed input tokens")
        return self


class ModelUsageManifest(TokenUsageManifest):
    estimated_cost_usd: str

    @model_validator(mode="after")
    def validate_cost(self) -> Self:
        _validate_cost(self.estimated_cost_usd)
        return self


class SegmentAttemptManifest(StrictManifestModel):
    model: str
    reasoning_effort: Literal["low", "medium", "high"]
    score: float = Field(ge=0, le=100)
    accepted: bool
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: str
    response_id: str
    request_id: str
    failure_reason: str | None
    stage: Literal["primary", "independent_consensus", "field_resolution"]
    peer_source_page: int | None = Field(default=None, ge=1)
    disagreement_count: int = Field(ge=0)
    batch_index: int | None = Field(default=None, ge=0)
    api_call_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_accounting(self) -> Self:
        TokenUsageManifest(
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
        )
        _validate_cost(self.estimated_cost_usd)
        if self.retry_count > self.api_call_count:
            raise ValueError("retry_count cannot exceed api_call_count")
        return self


class SegmentManifest(StrictManifestModel):
    segment_id: str
    segment_index: int = Field(ge=0)
    final_score: float = Field(ge=0, le=100)
    status: Literal[
        "accepted_quality", "accepted_consensus", "accepted_resolution", "needs_review"
    ]
    reasons: list[str]
    structural_conflicts: list[str]
    unresolved_fields: list[str]
    attempts: list[SegmentAttemptManifest]


class PageManifest(StrictManifestModel):
    source_page: int = Field(ge=1)
    status: Literal["ok", "failed"]
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: str
    elapsed_ms: int = Field(ge=0)
    range_repairs: int = Field(ge=0)
    attempts: int = Field(ge=0)
    api_call_count: int = Field(ge=0)
    routing_call_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    failure_reason: str | None
    response_id: str
    request_id: str
    models_used: list[str]
    usage_by_model: dict[str, ModelUsageManifest]
    segments: list[SegmentManifest]

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        if self.status == "failed" and not self.failure_reason:
            raise ValueError("failed pages require a failure_reason")
        if self.status == "ok" and self.failure_reason is not None:
            raise ValueError("successful pages cannot have a failure_reason")
        if self.routing_call_count > self.api_call_count:
            raise ValueError("routing_call_count cannot exceed api_call_count")
        if self.retry_count > self.api_call_count:
            raise ValueError("retry_count cannot exceed api_call_count")
        _validate_cost(self.estimated_cost_usd)
        usage = TokenUsageManifest(
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
        )
        if self.usage_by_model:
            _validate_usage_total(usage, list(self.usage_by_model.values()))
            if set(self.models_used) != set(self.usage_by_model):
                raise ValueError("models_used must match usage_by_model")
        return self


class AnnotationLimitationManifest(StrictManifestModel):
    source_page: int = Field(ge=1)
    element_id: str | None
    reason: str


class ArtifactHashManifest(StrictManifestModel):
    markdown_sha256: Sha256
    json_sha256: Sha256
    annotated_pdf_sha256: Sha256


class DocumentManifest(DocumentProvenanceFields):
    source_filename: str
    selected_pages: list[int]
    job_id: str
    model_provider: Literal["OpenAI"]
    endpoint: Literal["/v1/responses"]
    provider_response_storage: Literal[False]
    model: str
    model_cascade: list[ModelRouteManifest]
    peer_evidence_count: int = Field(ge=0)
    prompt_sha256: dict[str, Sha256]
    completed_page_count: int = Field(ge=0)
    failed_page_count: int = Field(ge=0)
    review_required: bool
    review_state: ReviewState
    needs_review_segment_count: int = Field(ge=0)
    usage: TokenUsageManifest
    estimated_cost_usd: str
    api_call_count: int = Field(ge=0)
    routing_call_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    pages: list[PageManifest]
    annotation_limitations: list[AnnotationLimitationManifest]
    artifact_sha256: ArtifactHashManifest

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        _validate_increasing_pages(self.selected_pages)
        page_numbers = [page.source_page for page in self.pages]
        if page_numbers != self.selected_pages:
            raise ValueError("manifest pages must match selected_pages in order")
        if [page.source_page for page in self.rendered_pages] != self.selected_pages:
            raise ValueError("rendered_pages must match selected_pages in order")
        failed_pages = sum(page.status == "failed" for page in self.pages)
        if self.failed_page_count != failed_pages:
            raise ValueError("failed_page_count does not match pages")
        if self.completed_page_count + self.failed_page_count != len(self.pages):
            raise ValueError("completed and failed page counts do not match pages")
        unresolved = sum(
            segment.status == "needs_review" for page in self.pages for segment in page.segments
        )
        if self.needs_review_segment_count != unresolved:
            raise ValueError("needs_review_segment_count does not match segments")
        expected_state = review_state(failed_pages=failed_pages, unresolved_segments=unresolved)
        if self.review_state != expected_state:
            raise ValueError("review_state does not match page and segment outcomes")
        if self.review_required != (expected_state != "not_required"):
            raise ValueError("review_required does not match review_state")
        _validate_usage_total(self.usage, self.pages)
        _validate_cost_total(self.estimated_cost_usd, self.pages)
        _validate_call_totals(self, self.pages)
        return self


class BatchFileManifest(StrictManifestModel):
    item_id: str
    source_filename: str
    selected_pages: list[int]
    status: Literal["ok", "partial", "failed"]
    review_required: bool
    review_state: ReviewState
    failure_reason: str | None
    usage: TokenUsageManifest
    estimated_cost_usd: str
    api_call_count: int = Field(ge=0)
    routing_call_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_file(self) -> Self:
        _validate_increasing_pages(self.selected_pages)
        _validate_cost(self.estimated_cost_usd)
        if self.status == "failed" and not self.failure_reason:
            raise ValueError("failed files require a failure_reason")
        if self.review_required != (self.review_state != "not_required"):
            raise ValueError("file review_required does not match review_state")
        if self.routing_call_count > self.api_call_count:
            raise ValueError("routing_call_count cannot exceed api_call_count")
        if self.retry_count > self.api_call_count:
            raise ValueError("retry_count cannot exceed api_call_count")
        return self


class BatchManifest(GovernanceManifestFields):
    file_count: int = Field(ge=1)
    successful_file_count: int = Field(ge=0)
    partial_file_count: int = Field(ge=0)
    failed_file_count: int = Field(ge=0)
    review_required: bool
    review_state: ReviewState
    review_required_file_count: int = Field(ge=0)
    selected_page_count: int = Field(ge=1)
    completed_page_count: int = Field(ge=0)
    failed_page_count: int = Field(ge=0)
    model_provider: Literal["OpenAI"]
    endpoint: Literal["/v1/responses"]
    provider_response_storage: Literal[False]
    model_cascade: list[ModelRouteManifest]
    usage: TokenUsageManifest
    estimated_cost_usd: str
    api_call_count: int = Field(ge=0)
    routing_call_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    files: list[BatchFileManifest]

    @model_validator(mode="after")
    def validate_batch(self) -> Self:
        if self.file_count != len(self.files):
            raise ValueError("file_count does not match files")
        status_counts = {
            status: sum(item.status == status for item in self.files)
            for status in ("ok", "partial", "failed")
        }
        if (
            self.successful_file_count,
            self.partial_file_count,
            self.failed_file_count,
        ) != (status_counts["ok"], status_counts["partial"], status_counts["failed"]):
            raise ValueError("file status counts do not match files")
        if self.selected_page_count != sum(len(item.selected_pages) for item in self.files):
            raise ValueError("selected_page_count does not match files")
        if self.completed_page_count + self.failed_page_count != self.selected_page_count:
            raise ValueError("completed and failed page counts do not match selected pages")
        review_files = sum(item.review_required for item in self.files)
        if self.review_required_file_count != review_files:
            raise ValueError("review_required_file_count does not match files")
        expected_state = review_state(
            failed_pages=self.failed_page_count,
            unresolved_segments=review_files - self.failed_file_count,
        )
        if self.review_state != expected_state:
            raise ValueError("review_state does not match batch outcomes")
        if self.review_required != (expected_state != "not_required"):
            raise ValueError("review_required does not match review_state")
        _validate_usage_total(self.usage, [item.usage for item in self.files])
        _validate_cost_total(self.estimated_cost_usd, self.files)
        _validate_call_totals(self, self.files)
        return self


def _validate_cost(value: str) -> Decimal:
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("estimated cost must be a decimal string") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError("estimated cost must be finite and nonnegative")
    return amount


def _validate_increasing_pages(pages: list[int]) -> None:
    if not pages or pages != sorted(set(pages)) or pages[0] < 1:
        raise ValueError("selected pages must be positive, unique, and increasing")


def _usage_values(value: Any) -> tuple[int, int, int, int, int]:
    usage = getattr(value, "usage", value)
    return (
        usage.input_tokens,
        usage.cached_input_tokens,
        usage.cache_write_tokens,
        usage.output_tokens,
        usage.reasoning_tokens,
    )


def _validate_usage_total(total: TokenUsageManifest, values: list[Any]) -> None:
    expected = tuple(sum(parts) for parts in zip(*map(_usage_values, values), strict=True))
    if _usage_values(total) != expected:
        raise ValueError("aggregate usage does not match child records")


def _validate_cost_total(total: str, values: list[Any]) -> None:
    expected = sum((_validate_cost(value.estimated_cost_usd) for value in values), Decimal(0))
    if _validate_cost(total) != expected:
        raise ValueError("aggregate cost does not match child records")


def _validate_call_totals(total: Any, values: list[Any]) -> None:
    for field in ("api_call_count", "routing_call_count", "retry_count"):
        if getattr(total, field) != sum(getattr(value, field) for value in values):
            raise ValueError(f"{field} does not match child records")
    if total.routing_call_count > total.api_call_count:
        raise ValueError("routing_call_count cannot exceed api_call_count")
    if total.retry_count > total.api_call_count:
        raise ValueError("retry_count cannot exceed api_call_count")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def app_version() -> str:
    try:
        return version("ade-app")
    except PackageNotFoundError:
        return "0+unknown"


def governance_policy_fields() -> dict[str, Any]:
    fields = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "governance_policy_version": "2026-09-01",
        "intended_use": "human-reviewed document extraction; not payer adjudication",
        "data_classification": "potentially_sensitive_health_document",
        "sensitive_metadata_warning": (
            "filenames and generated artifacts may contain sensitive data"
        ),
        "canonical_markdown_trusted": False,
        "evaluation_artifacts_sensitive": True,
        "governance_policy_sha256": {
            path.name: sha256_bytes(path.read_bytes()) if path.is_file() else None
            for path in _POLICY_PATHS
        },
    }
    return GovernanceManifestFields.model_validate(fields).model_dump(mode="json")


def provenance_fields(
    source: DocumentInput,
    rendered_pages: tuple[RenderedPage, ...],
    *,
    dpi: int = DEFAULT_DPI,
) -> dict[str, Any]:
    """Return identifiers and runtime configuration, never source content or credentials."""

    profile_path = Path(QUALITY_PROFILE_PATH)
    profile_bytes = profile_path.read_bytes() if profile_path.is_file() else None
    validation = json.loads(profile_bytes).get("validation", {}) if profile_bytes else {}
    fields = {
        **governance_policy_fields(),
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "application": {"name": "ade-app", "version": app_version()},
        "source_sha256": sha256_bytes(source.data),
        "raster_dpi": dpi,
        "rendered_pages": [
            {
                "source_page": page.source_page,
                "sha256": sha256_bytes(page.png_bytes),
                "width": page.width,
                "height": page.height,
            }
            for page in rendered_pages
        ],
        "quality_profile": {
            "path": profile_path.as_posix(),
            "sha256": sha256_bytes(profile_bytes) if profile_bytes else None,
            "routing_mode": (
                "repair_all"
                if profile_bytes and int(validation.get("accepted_count", 0)) == 0
                else "quality_gated"
            ),
        },
    }
    return DocumentProvenanceFields.model_validate(fields).model_dump(mode="json")


def review_state(*, failed_pages: int, unresolved_segments: int) -> ReviewState:
    if failed_pages < 0 or unresolved_segments < 0:
        raise ValueError("review counts cannot be negative")
    if failed_pages:
        return "failed"
    if unresolved_segments:
        return "required_unresolved"
    return "not_required"


def artifact_hashes(markdown: str, json_text: str, annotated_pdf: bytes) -> dict[str, str]:
    fields = {
        "markdown_sha256": sha256_bytes(markdown.encode("utf-8")),
        "json_sha256": sha256_bytes(json_text.encode("utf-8")),
        "annotated_pdf_sha256": sha256_bytes(annotated_pdf),
    }
    return ArtifactHashManifest.model_validate(fields).model_dump(mode="json")


def validate_document_manifest(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and serialize the complete document manifest contract."""

    return DocumentManifest.model_validate(value).model_dump(mode="json")


def validate_batch_manifest(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and serialize the complete batch manifest contract."""

    return BatchManifest.model_validate(value).model_dump(mode="json")
