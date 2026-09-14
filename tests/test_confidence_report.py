from dataclasses import dataclass
from typing import Literal
from unittest.mock import Mock

from ade_app.services.confidence import build_confidence_report


@dataclass
class Record:
    source_page: int
    status: Literal["ok", "failed"]
    models_used: tuple[str, ...] = ()
    segments: tuple = ()
    failure_reason: str | None = None


def test_failed_page_forces_review_and_zero_confidence() -> None:
    artifact = Mock(fields=[])
    report = build_confidence_report(
        [Record(1, "failed", failure_reason="layout failed")], artifact
    )

    assert report.document_confidence == 0
    assert report.review_required is True
    assert report.pages[0].review_reasons == ["layout failed"]
    assert report.schema_version == "2.0"
    assert report.failed_page_count == 1
