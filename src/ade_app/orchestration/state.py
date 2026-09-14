"""Explicit typed hand-offs for the document workflow.

StageName/NextAction enumerate the graph's nodes and edge labels; graph.py's
conditional-edge maps must stay in sync with these two Literals — add a value
here and a matching edge there together, or the compiled graph raises at
either compile or dispatch time. See graph.py next for how these drive routing,
and nodes.py for what actually produces each stage's Mapping payload.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import ConfigDict, Field

from ade_app.contracts import ExtractionRun, PageRunRecord
from ade_app.cost import TokenUsage
from ade_app.fields import RawFieldCandidate
from ade_app.inputs import DocumentInput
from ade_app.layout import LayoutAnalysis
from ade_app.models import ExtractedField, StrictModel
from ade_app.preprocessing import PageIngestionError, PreparedPage
from ade_app.raster import RenderedPage
from ade_app.rendering import PageOutcome

StageName = Literal[
    "ingest_preprocess",
    "layout_analysis",
    "route_and_extract",
    "validate_and_link",
    "generate_outputs",
]
NextAction = Literal["continue", "retry", "sol_retry", "validate", "generate", "done", "error"]


class WorkflowError(StrictModel):
    stage: StageName
    message: str
    retryable: bool = False
    source_page: int | None = Field(default=None, ge=1)
    attempt: int = Field(default=0, ge=0)


class WorkflowConfig(StrictModel):
    max_graph_retries: int = Field(default=1, ge=0, le=2)
    max_concurrency: int = Field(default=3, ge=1)
    retry_failed_fields_with_sol: bool = False


@dataclass(frozen=True, slots=True)
class WorkflowRequest:
    source: DocumentInput
    pages: tuple[int, ...]
    dpi: int
    rendered_pages: tuple[RenderedPage, ...] | None = None
    progress: Callable[[int, int, int, int, str], None] | None = None


@dataclass(frozen=True, slots=True)
class IngestionStageResult:
    page_count: int
    prepared_pages: tuple[PreparedPage, ...]
    rendered_pages: tuple[RenderedPage, ...]
    errors: tuple[PageIngestionError, ...]


@dataclass(frozen=True, slots=True)
class LayoutPageResult:
    prepared: PreparedPage
    analysis: LayoutAnalysis | None


@dataclass(frozen=True, slots=True)
class LayoutStageResult:
    pages: tuple[LayoutPageResult, ...]


@dataclass(frozen=True, slots=True)
class ExtractionStageResult:
    outcomes: tuple[PageOutcome, ...]
    page_records: tuple[PageRunRecord, ...]
    raw_fields: tuple[RawFieldCandidate, ...]
    usage: TokenUsage
    cost_usd: Decimal
    service_tier: str
    model_version: str
    peer_evidence_count: int
    duration_ms: int
    sol_resolutions: dict[str, tuple[str | bool, float]] | None = None


@dataclass(frozen=True, slots=True)
class SolRetryPlan:
    mandatory_region_ids: tuple[str, ...] = ()
    fields: tuple[ExtractedField, ...] = ()

    @property
    def required(self) -> bool:
        return bool(self.mandatory_region_ids or self.fields)


@dataclass(frozen=True, slots=True)
class ValidationStageResult:
    fields: tuple[ExtractedField, ...]
    untrusted_elements: dict[int, set[str]]
    needs_review_segment_count: int
    needs_review_field_count: int
    retry_plan: SolRetryPlan | None = None


# The seam between graph mechanics (this package) and actual pipeline behavior:
# pipeline.py's _DocumentWorkflowOperations is the real implementation the graph drives.
# @runtime_checkable only checks method names/arity exist, not signatures — tests rely on
# this to swap in fakes without the graph knowing.
@runtime_checkable
class WorkflowOperations(Protocol):
    def ingest_preprocess(self, request: WorkflowRequest) -> IngestionStageResult: ...
    def layout_analysis(self, ingestion: IngestionStageResult) -> LayoutStageResult: ...
    def route_and_extract(
        self,
        layout: LayoutStageResult,
        ingestion: IngestionStageResult,
        previous: ExtractionStageResult | None,
        retry_plan: SolRetryPlan | None,
    ) -> ExtractionStageResult: ...
    def validate_and_link(self, extraction: ExtractionStageResult) -> ValidationStageResult: ...
    def generate_outputs(
        self,
        ingestion: IngestionStageResult,
        extraction: ExtractionStageResult,
        validation: ValidationStageResult,
    ) -> ExtractionRun: ...


class DocumentWorkflowState(StrictModel):
    # arbitrary_types_allowed is required here (unlike plain StrictModel) because this state
    # carries non-Pydantic runtime objects — the WorkflowOperations implementation and the
    # progress callback — that a strict schema would otherwise reject.
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    operations: WorkflowOperations
    request: WorkflowRequest
    config: WorkflowConfig
    ingestion: IngestionStageResult | None = None
    layout: LayoutStageResult | None = None
    extraction: ExtractionStageResult | None = None
    validation: ValidationStageResult | None = None
    retry_plan: SolRetryPlan | None = None
    output: ExtractionRun | None = None
    next_action: NextAction = "continue"
    route_mode: Literal["initial", "sol_retry"] = "initial"
    retry_count: int = Field(default=0, ge=0)
    sol_retry_count: int = Field(default=0, ge=0)
    # Annotated[..., operator.add]: a LangGraph reducer, not an ordinary field default.
    # When more than one node returns an "errors" update in the same step (e.g. concurrent
    # page fan-out), LangGraph concatenates the lists with operator.add instead of the last
    # write winning — errors accumulate across retries rather than being overwritten.
    errors: Annotated[list[WorkflowError], operator.add] = Field(default_factory=list)


class PageState(StrictModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    page: RenderedPage
    page_runner: Callable[[RenderedPage], object]
    # Same operator.add reducer as above: each Send'd run_page invocation contributes one
    # result, and LangGraph merges them by list concatenation, not overwrite.
    results: Annotated[list[object], operator.add] = Field(default_factory=list)


class DocumentState(StrictModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    pages: tuple[RenderedPage, ...]
    page_runner: Callable[[RenderedPage], object]
    results: Annotated[list[object], operator.add] = Field(default_factory=list)
