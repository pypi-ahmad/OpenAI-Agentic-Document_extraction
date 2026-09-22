import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from ade_app.cli import _parser, _settings
from ade_app.config import PipelineConfig, StageSettings
from ade_app.cost import TokenUsage, calculate_cost
from ade_app.drafts import DraftDocument, build_draft
from ade_app.hybrid import HybridPageExtractor, routing_fingerprint
from ade_app.inputs import DocumentInput
from ade_app.models import (
    FieldResolution,
    PostProcessingResolutionBatch,
    SegmentPatchAudit,
    SemanticFieldResolutionBatch,
    SemanticLineValue,
    SemanticSegmentPatch,
    SemanticText,
)
from ade_app.openai_client import OpenAIPageExtractor
from ade_app.pipeline import extract_document
from ade_app.quality import load_quality_profile
from ade_app.raster import RenderedPage
from ade_app.rendering import PageOutcome


@pytest.mark.parametrize(
    "verification,repair,expected_calls",
    [
        (False, False, 1),
        (True, False, 2),
        (False, True, 2),
        (True, True, 3),
    ],
)
def test_stage_matrix_preserves_draft_and_single_model_accounting(
    audited_semantic_page,
    monkeypatch,
    verification,
    repair,
    expected_calls,
):
    initial = audited_semantic_page.model_copy(deep=True)
    initial.children[0].lines[0].content = [SemanticText(text="Patient name: Jane Doe")]
    initial.children[0].lines[0].style = "plain"
    calls = []

    class Responses:
        def parse(self, **kwargs):
            calls.append(kwargs)
            stage = kwargs["metadata"]["attempt"]
            if stage.startswith("full-"):
                parsed = initial
            elif stage == "independent-consensus":
                element = initial.children[0].model_copy(deep=True)
                element.lines[0].content = [SemanticText(text="Patient name: Janet Doe")]
                parsed = SemanticSegmentPatch(
                    segment_id="p1-s0",
                    element=element,
                    audit=SegmentPatchAudit(
                        segment_index=0,
                        completeness="complete",
                        image_agreement="supported",
                        findings=[],
                    ),
                )
            elif stage == "field-resolution":
                parsed = SemanticFieldResolutionBatch(
                    segment_id="p1-s0",
                    resolutions=[
                        FieldResolution(
                            field_id="line-0",
                            kind="line",
                            status="resolved",
                            line=SemanticLineValue(
                                content=[SemanticText(text="Patient name: Jane Doe")], style="plain"
                            ),
                        )
                    ],
                )
            elif stage == "postprocessing-repair":
                fields = [
                    json.loads(item["text"])
                    for item in kwargs["input"][0]["content"]
                    if item["type"] == "input_text"
                ]
                parsed = PostProcessingResolutionBatch.model_validate(
                    {
                        "resolutions": [
                            {"field_id": field["field_id"], "value": "Jane Doe", "confidence": 90.0}
                            for field in fields
                        ]
                    }
                )
            else:
                raise AssertionError(stage)
            return SimpleNamespace(
                output_parsed=parsed,
                id=f"response-{len(calls)}",
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )

    config = PipelineConfig(stages=StageSettings(verification=verification, repair=repair))
    image = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(image, "PNG")
    source = DocumentInput("sample.png", image.getvalue())
    page = RenderedPage(1, source.data, 100, 100)
    extractor = HybridPageExtractor(
        OpenAIPageExtractor(Responses(), profile_path=None, config=config)
    )
    monkeypatch.setattr(extractor, "analyze_prepared", lambda page: None)
    run = extract_document(source, (1,), extractor, rendered_pages=(page,), config=config)
    assert len(calls) == expected_calls
    assert all(
        call["model"] == "gpt-6-sol"
        and call["reasoning"] == {"effort": "medium"}
        and call["store"] is False
        for call in calls
    )
    assert sum(record.api_call_count for record in run.pages) == expected_calls
    assert sum(record.routing_call_count for record in run.pages) == expected_calls - 1
    assert run.usage == TokenUsage(
        input_tokens=expected_calls * 100, output_tokens=expected_calls * 20
    )
    assert run.cost_usd == calculate_cost(run.usage)
    assert run.pages[0].models_used == ("gpt-6-sol",)
    assert len(run.pages[0].usage_by_model) == 1
    assert run.manifest["stages"] == {"verification": verification, "repair": repair}
    assert run.manifest["manifest_schema_version"] == 10
    assert "model_cascade" not in run.manifest
    draft = DraftDocument.model_validate_json(run.draft_json_text)
    assert draft.artifact_kind == "unverified_draft"
    assert draft.pages[0].extraction is not None
    assert "Jane Doe" in draft.pages[0].extraction.markdown
    assert "Janet Doe" not in run.draft_markdown
    if not verification and not repair:
        assert "Jane Doe" not in run.artifact.markdown
        assert all(field.value is None for field in run.artifact.fields)
        assert run.manifest["review_required"] is True
    with zipfile.ZipFile(io.BytesIO(run.zip_bytes)) as archive:
        assert archive.read("sample.draft.json").decode() == run.draft_json_text


def test_old_model_configuration_and_calibration_are_rejected(tmp_path):
    for values in ({"name": "gpt-5.6-sol"}, {"reasoning_effort": "low"}):
        with pytest.raises(ValidationError):
            PipelineConfig.model_validate({"model": values})
    path = tmp_path / "settings.toml"
    path.write_text('[models.sol]\nname = "gpt-5.6-sol"\n')
    with pytest.raises(ValueError, match="legacy"):
        PipelineConfig.from_toml(path)
    with pytest.raises(RuntimeError, match="active verification model"):
        load_quality_profile("profiles/segment-quality-terra-v3.json")


def test_stage_toggles_default_off_and_reset_without_api_calls(monkeypatch):
    create = Mock(side_effect=AssertionError("UI toggles must not make API calls"))
    monkeypatch.setattr("ade_app.runner.create_extractor", create)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "streamlit_app.py")).run()
    assert not app.exception
    assert [toggle.value for toggle in app.toggle] == [False, False]
    app.toggle[0].set_value(True).run()
    app.toggle[1].set_value(True).run()
    assert [toggle.value for toggle in app.toggle] == [True, True]
    next(button for button in app.button if button.label == "Reset").click().run()
    assert [toggle.value for toggle in app.toggle] == [False, False]
    assert not app.exception
    create.assert_not_called()


def test_cli_stage_overrides_and_fingerprints(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text("[stages]\nverification = true\nrepair = false\n")
    args = _parser().parse_args(
        [
            "input.png",
            "--config",
            str(path),
            "--no-verification",
            "--repair",
        ]
    )
    config = _settings(args)
    assert config.stages == StageSettings(verification=False, repair=True)
    assert config.model == PipelineConfig().model
    assert routing_fingerprint(config) != routing_fingerprint(PipelineConfig())


def test_failed_page_is_explicit_in_draft():
    markdown, payload = build_draft(
        "failed.png",
        (PageOutcome(source_page=1, extraction=None, failure_reason="Extraction failed"),),
    )
    draft = DraftDocument.model_validate_json(payload)
    assert draft.pages[0].status == "failed"
    assert draft.pages[0].extraction is None
    assert markdown.startswith("# Unverified extraction draft")
    assert "[PAGE EXTRACTION FAILED]" in markdown
