"""Deterministic confidence report generation.

Builds the strict schemas/confidence.py ConfidenceReport from page run records and
a v2/v3 extraction artifact. Must not fabricate a confidence score where the pipeline
has none — see the "unknown probability" note on build_confidence_report. Continue to
schemas/confidence.py for the report shape this produces.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, Protocol, cast

from ade_app.models import ExtractedField, ExtractionDocumentV2, ExtractionDocumentV3
from ade_app.schemas.confidence import ConfidenceReport, FieldConfidence, PageConfidence, ReviewItem


# Structural protocol so this module doesn't need to import the concrete page-run-record
# type (avoids a dependency on the orchestration layer); any object with this shape works.
class PageRecord(Protocol):
    @property
    def source_page(self) -> int: ...

    @property
    def status(self) -> Literal["ok", "failed"]: ...

    @property
    def models_used(self) -> tuple[str, ...]: ...

    @property
    def segments(self) -> tuple: ...

    @property
    def failure_reason(self) -> str | None: ...


def build_confidence_report(
    records: Iterable[PageRecord],
    artifact: ExtractionDocumentV2 | ExtractionDocumentV3,
    *,
    rejected_fields: Iterable[ExtractedField] = (),
) -> ConfidenceReport:
    """Report unknown probabilities honestly and include every review item."""

    pages = []

    for record in records:
        reasons = ([record.failure_reason] if record.failure_reason else []) + [
            reason
            for segment in record.segments
            if segment.status == "needs_review"
            for reason in (*segment.reasons, "segment_needs_review")
        ]

        if not record.segments and record.status != "failed":
            reasons.append("missing_evidence")

        pages.append(
            PageConfidence(
                source_page=record.source_page,
                status=record.status,
                # A failed page's confidence is a known 0.0; a page that ran has no single
                # measured score, so it is reported as None rather than an invented number.
                confidence=0.0 if record.status == "failed" else None,
                models_used=list(record.models_used),
                review_reasons=list(dict.fromkeys(reasons)),
            )
        )

    # unclear from this file why this import is deferred rather than module-level; see fields.py
    from ade_app.fields import public_fields

    selected = (
        list(artifact.fields)
        if isinstance(artifact, ExtractionDocumentV3)
        else public_fields(artifact.fields)
    )

    selected.extend(public_fields(rejected_fields))

    fields = [
        FieldConfidence(
            field_id=field.field_id,
            canonical_name=field.canonical_name,
            confidence=field.confidence,
            status=field.status,
            review_reasons=field.reasons,
            selected_value=field.value,
            evidence_pages=sorted({e.page for e in field.evidence}),
            evidence_routes=list(dict.fromkeys(e.route for e in field.evidence)),
            validation_checks=[
                f"{c.rule}: {'pass' if c.passed else 'fail'} — {c.message}"
                for c in field.validation_checks
            ],
        )
        for field in selected
    ]

    review_fields = sorted(
        (field for field in fields if field.status != "accepted"),
        key=lambda field: (field.confidence or 0, field.canonical_name),
    )

    queue = [
        ReviewItem(
            priority=index,
            field_id=field.field_id,
            canonical_name=field.canonical_name,
            status=cast(Literal["needs_review", "conflict"], field.status),
            confidence=field.confidence,
            reasons=field.review_reasons,
        )
        for index, field in enumerate(review_fields, 1)
    ]

    return ConfidenceReport(
        document_confidence=0.0 if any(p.status == "failed" for p in pages) else None,
        review_required=bool(queue) or any(p.review_reasons or p.status == "failed" for p in pages),
        pages=pages,
        fields=fields,
        accepted_field_count=sum(f.status == "accepted" for f in fields),
        review_field_count=len(queue),
        failed_page_count=sum(p.status == "failed" for p in pages),
        review_queue=queue,
    )
