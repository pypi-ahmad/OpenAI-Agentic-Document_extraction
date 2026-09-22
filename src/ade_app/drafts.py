"""Unverified primary extraction artifacts, separate from verified JSON v3."""

from typing import Literal

from ade_app.constants import MODEL_ID
from ade_app.models import PageExtraction, StrictModel
from ade_app.rendering import PageOutcome


class DraftPage(StrictModel):
    source_page: int
    status: Literal["ok", "failed"]
    extraction: PageExtraction | None
    failure_reason: str | None = None


class DraftDocument(StrictModel):
    draft_schema_version: Literal[1] = 1
    artifact_kind: Literal["unverified_draft"] = "unverified_draft"
    verification_status: Literal["unverified"] = "unverified"
    model: Literal["gpt-6-sol"] = MODEL_ID
    reasoning_effort: Literal["medium"] = "medium"
    source_filename: str
    pages: list[DraftPage]


def build_draft(filename: str, outcomes: tuple[PageOutcome, ...]) -> tuple[str, str]:
    """Retain page-local grounding ranges and explicitly report failed pages."""
    pages = []
    markdown = ["# Unverified extraction draft\n\nRequires review. Values are not verified."]
    for outcome in outcomes:
        candidate = outcome.draft_extraction or outcome.candidate_extraction or outcome.extraction
        pages.append(
            DraftPage(
                source_page=outcome.source_page,
                status="ok" if candidate is not None else "failed",
                extraction=candidate,
                failure_reason=outcome.failure_reason,
            )
        )
        markdown.append(f"<!-- SOURCE PAGE {outcome.source_page} -->")
        markdown.append(candidate.markdown if candidate else "[PAGE EXTRACTION FAILED]")
    draft = DraftDocument(source_filename=filename, pages=pages)
    return "\n\n".join(markdown), draft.model_dump_json(indent=2)
