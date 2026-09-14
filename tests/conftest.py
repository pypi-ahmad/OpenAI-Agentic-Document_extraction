from __future__ import annotations

import pytest

from ade_app.models import (
    AuditedSemanticPageExtraction,
    Box,
    DraftGrounding,
    DraftLeaf,
    PageExtraction,
    SegmentAudit,
    SemanticLeaf,
    SemanticLine,
    SemanticText,
    TextRange,
)
from ade_app.quality import FEATURE_NAMES, QualityProfile


@pytest.fixture(autouse=True)
def offline_orientation(monkeypatch) -> None:
    """Keep unit tests independent of native model loading and downloads."""
    monkeypatch.setattr("ade_app.preprocessing._predict_orientation", lambda image: (0, 0.99))


@pytest.fixture
def sample_page() -> PageExtraction:
    markdown = "# Sample heading"
    grounding = DraftGrounding(
        range=TextRange(start=0, end=len(markdown)),
        box=Box(xmin=0.1, ymin=0.1, xmax=0.9, ymax=0.2),
    )
    return PageExtraction(
        markdown=markdown,
        children=[
            DraftLeaf(
                type="text",
                grounding=grounding,
                atomic_grounding=[grounding],
            )
        ],
    )


@pytest.fixture
def audited_semantic_page() -> AuditedSemanticPageExtraction:
    box = Box(xmin=0.1, ymin=0.1, xmax=0.9, ymax=0.2)
    return AuditedSemanticPageExtraction(
        children=[
            SemanticLeaf(
                type="text",
                lines=[
                    SemanticLine(
                        content=[SemanticText(text="Sample heading")],
                        box=box,
                        style="heading_1",
                    )
                ],
                box=box,
            )
        ],
        audits=[
            SegmentAudit(
                segment_index=0,
                completeness="complete",
                image_agreement="supported",
                findings=[],
            )
        ],
    )


@pytest.fixture
def quality_profile() -> QualityProfile:
    return QualityProfile(
        feature_names=FEATURE_NAMES,
        coefficients=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        intercept=5.0,
        threshold=90.0,
        prompt_hashes={},
        groundtruth_hashes={},
        validation={"false_accept_rate": 0.0},
    )
