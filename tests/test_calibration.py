from pathlib import Path

import pytest

from ade_app.calibration import _profile_can_be_promoted, _select_pages
from ade_app.corpus import CorpusDocument


def _document(stem: str, page_count: int) -> CorpusDocument:
    return CorpusDocument(
        stem=stem,
        source=Path(f"{stem}.pdf"),
        groundtruth_json=Path(f"{stem}.parse.json"),
        groundtruth_markdown=Path(f"{stem}.parse.md"),
        page_count=page_count,
        pages=tuple(range(1, page_count + 1)),
    )


def test_curated_calibration_uses_exact_evaluation_pages() -> None:
    assert _select_pages(_document("Masked BadgeCare Plus_1", 13), "curated") == (1, 2)
    assert _select_pages(_document("PublicWaterMassMailing", 8), "curated") == (4, 5)


def test_calibration_rejects_unknown_suite() -> None:
    with pytest.raises(ValueError, match="unsupported calibration suite"):
        _select_pages(_document("sample", 1), "unknown")


def test_calibration_does_not_promote_zero_acceptance_profile() -> None:
    assert not _profile_can_be_promoted({"accepted_count": 0, "false_accept_rate": 0.0}, [])
    assert not _profile_can_be_promoted({"accepted_count": 1, "false_accept_rate": 0.10}, [])
    assert _profile_can_be_promoted(
        {
            "accepted_count": 1,
            "false_accept_rate": 0,
            "promotion_passed": 1,
            "held_out_family_count": 3,
        },
        [],
    )
