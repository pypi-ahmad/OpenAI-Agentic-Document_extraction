"""Safe high-level execution wrapper for single document extraction.

Responsible for configuring model pricing, instantiating extractors (`create_extractor`),
running document extraction without throwing unhandled exceptions (`run_pipeline`),
and returning `PipelineResult`.
Must NOT let unexpected processing failures bubble up to callers unhandled.
Next: ade_app.cli which invokes run_pipeline and translates results into exit codes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from ade_app.config import PipelineConfig
from ade_app.cost import configure_model_rates
from ade_app.hybrid import HybridPageExtractor
from ade_app.inputs import DocumentInput
from ade_app.layout import PPStructureAnalyzer
from ade_app.models import StrictModel
from ade_app.openai_client import OpenAIPageExtractor, build_responses_parser, resolve_api_key
from ade_app.pipeline import ExtractionRun, extract_document
from ade_app.spending import BudgetedResponses, SpendLedger

logger = logging.getLogger(__name__)


def create_extractor(
    config: PipelineConfig | None = None,
    *,
    api_key: str | None = None,
    ledger: SpendLedger | None = None,
) -> HybridPageExtractor:
    """Construct the same production pipeline for UI, CLI and evaluation."""
    settings = config or PipelineConfig()
    configure_model_rates(settings)
    responses = build_responses_parser(resolve_api_key(api_key), settings)
    if ledger is not None:
        responses = BudgetedResponses(responses, ledger, settings)
    return HybridPageExtractor(
        OpenAIPageExtractor(responses, config=settings),
        analyzer=PPStructureAnalyzer(settings.layout),
    )


class PipelineIssue(StrictModel):
    code: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False
    source_page: int | None = Field(default=None, ge=1)
    cause_type: str | None = None


@dataclass(frozen=True, slots=True)
class PipelineResult:
    status: Literal["complete", "partial", "failed"]
    run: ExtractionRun | None
    issues: tuple[PipelineIssue, ...]
    report_text: str


def run_pipeline(
    source: DocumentInput,
    pages: tuple[int, ...],
    *,
    config: PipelineConfig | None = None,
) -> PipelineResult:
    """Run the full pipeline without raising expected document-processing failures."""

    settings = config or PipelineConfig()
    configure_model_rates(settings)
    try:
        extractor = create_extractor(settings)
        run = extract_document(
            source,
            pages,
            extractor,
            dpi=settings.imaging.dpi,
            max_workers=settings.runtime.max_page_workers,
            retry_failed_fields=settings.stages.repair,
            max_graph_retries=settings.retries.graph_max_page_retries,
            config=settings,
        )
    except Exception as error:
        issue = PipelineIssue(
            code="pipeline_failed",
            stage="pipeline",
            message="Document extraction failed; no parse artifact was produced",
            cause_type=type(error).__name__,
        )
        logger.error(
            issue.message,
            extra={
                "event": "pipeline_failed",
                "stage": issue.stage,
                "error_code": issue.cause_type,
            },
        )
        report = {"status": "failed", "issues": [issue.model_dump(mode="json")]}
        return PipelineResult("failed", None, (issue,), json.dumps(report, indent=2))

    partial = bool(run.manifest.get("review_required") or run.annotation_limitations)
    issues = (
        (
            PipelineIssue(
                code="review_required",
                stage="validation",
                message="Extraction completed with fields or pages requiring human review",
            ),
        )
        if partial
        else ()
    )
    report = {
        "status": "partial" if partial else "complete",
        "job_id": run.manifest.get("job_id"),
        "issues": [issue.model_dump(mode="json") for issue in issues],
    }
    return PipelineResult(
        "partial" if partial else "complete",
        run,
        issues,
        json.dumps(report, indent=2),
    )
