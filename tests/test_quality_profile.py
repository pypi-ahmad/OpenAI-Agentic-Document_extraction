from pathlib import Path

import pytest

from ade_app.constants import VERIFICATION_MODEL
from ade_app.openai_client import OpenAIPageExtractor
from ade_app.quality import FEATURE_NAMES, QualityProfile, load_quality_profile


def _profile(*, accepted_count: int = 0, model: str = VERIFICATION_MODEL[0]) -> QualityProfile:
    return QualityProfile(
        profile_version=3,
        feature_names=FEATURE_NAMES,
        coefficients=(0.0,) * len(FEATURE_NAMES),
        intercept=0.0,
        threshold=90.0,
        prompt_hashes={"page_extraction.md": "stale"},
        groundtruth_hashes={},
        validation={"accepted_count": accepted_count},
        raw_decision_boundary=100.0,
        model_id=model,
        reasoning_effort=VERIFICATION_MODEL[1],
    )


def test_quality_profile_targets_verification_model(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(_profile().model_dump_json(), encoding="utf-8")

    assert load_quality_profile(path).model_id == VERIFICATION_MODEL[0]

    path.write_text(_profile(model="gpt-5.6-luna").model_dump_json(), encoding="utf-8")
    with pytest.raises(RuntimeError, match="verification model"):
        load_quality_profile(path)


def test_stale_prompt_is_allowed_only_for_repair_all() -> None:
    OpenAIPageExtractor(object(), profile=_profile())

    extractor = OpenAIPageExtractor(object(), profile=_profile(accepted_count=1))
    assert not extractor._profile_trusted
