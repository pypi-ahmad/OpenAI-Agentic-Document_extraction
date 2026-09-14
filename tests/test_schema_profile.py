from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ade_app.models import GroundTruthDocument
from ade_app.profile import check_profile_artifacts, discover_groundtruth_profile

GROUNDTRUTHS = Path("data/GroundTruths")


def test_all_local_groundtruth_pairs_validate() -> None:
    json_files = sorted(GROUNDTRUTHS.glob("*.parse.json"))
    assert len(json_files) == 5
    for path in json_files:
        artifact = GroundTruthDocument.model_validate_json(path.read_text(encoding="utf-8"))
        assert artifact.markdown == path.with_suffix(".md").read_text(encoding="utf-8")


def test_strict_schema_rejects_unknown_field() -> None:
    path = next(GROUNDTRUTHS.glob("*.parse.json"))
    value = json.loads(path.read_text(encoding="utf-8"))
    value["invented"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GroundTruthDocument.model_validate(value)


def test_profile_is_content_free_and_current() -> None:
    profile = discover_groundtruth_profile(GROUNDTRUTHS)
    assert profile["pair_count"] == 5
    assert profile["source_page_count"] == 81
    assert profile["element_counts"]["table_cell"] == 1214
    serialized = json.dumps(profile)
    assert "Amerigroup" not in serialized
    assert "Missouri Department" not in serialized
    check_profile_artifacts()
