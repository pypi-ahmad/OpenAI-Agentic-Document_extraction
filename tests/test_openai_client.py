from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
import pytest
from openai import APIConnectionError
from PIL import Image

from ade_app.config import PipelineConfig, StageSettings
from ade_app.fields import discover_raw_fields
from ade_app.models import (
    AuditedSemanticPageExtraction,
    DraftLeaf,
    FieldResolution,
    PageExtraction,
    SegmentAudit,
    SegmentPatchAudit,
    SemanticFieldResolutionBatch,
    SemanticLeaf,
    SemanticLineValue,
    SemanticSegmentPatch,
    SemanticText,
)
from ade_app.openai_client import (
    CONSENSUS_MAX_OUTPUT_TOKENS,
    FIELD_RESOLUTION_MAX_OUTPUT_TOKENS,
    FULL_PAGE_MAX_OUTPUT_TOKENS,
    MAX_CONCURRENT_RESPONSES,
    OPENAI_TIMEOUT_SECONDS,
    OpenAIPageExtractor,
    StructuredOutputError,
    build_responses_parser,
    resolve_api_key,
    resolve_openai_base_url,
    validate_page_extraction,
)
from ade_app.quality import FEATURE_NAMES, QualityProfile
from ade_app.raster import RenderedPage
from ade_app.rendering import PageOutcome


class FakeResponses:
    def __init__(self, extraction: AuditedSemanticPageExtraction) -> None:
        self.extraction = extraction
        self.kwargs: dict[str, Any] = {}
        self.create_kwargs: dict[str, Any] = {}
        self.create_calls = 0

    def create(self, **kwargs: Any) -> Any:
        self.create_calls += 1
        self.create_kwargs = kwargs
        return SimpleNamespace(
            output_text="",
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                input_tokens_details=SimpleNamespace(cached_tokens=10, cache_write_tokens=5),
                output_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
        )

    def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return SimpleNamespace(
            output_parsed=self.extraction,
            id="resp_test",
            _request_id="req_test",
            service_tier="default",
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                input_tokens_details=SimpleNamespace(cached_tokens=10, cache_write_tokens=5),
                output_tokens_details=SimpleNamespace(reasoning_tokens=3),
            ),
        )


class FailOnceResponses(FakeResponses):
    def __init__(self, extraction: AuditedSemanticPageExtraction) -> None:
        super().__init__(extraction)
        self.calls = 0

    def parse(self, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            raise ValueError("invalid structured output")
        return super().parse(**kwargs)


class AlwaysFailResponses(FakeResponses):
    def __init__(self, extraction: AuditedSemanticPageExtraction) -> None:
        super().__init__(extraction)
        self.calls = 0

    def parse(self, **kwargs: Any) -> Any:
        self.calls += 1
        raise ValueError("invalid structured output")


class ReturnedInvalidResponses(FakeResponses):
    def __init__(self, extraction: AuditedSemanticPageExtraction, *, always: bool = False) -> None:
        super().__init__(extraction)
        self.always = always
        self.calls = 0

    def parse(self, **kwargs: Any) -> Any:
        self.calls += 1
        response = super().parse(**kwargs)
        if self.always or self.calls == 1:
            response.output_parsed = None
        return response


def test_primary_luna_call_uses_private_bounded_schema(
    audited_semantic_page: AuditedSemanticPageExtraction,
    quality_profile,
) -> None:
    responses = FakeResponses(audited_semantic_page)
    extractor = OpenAIPageExtractor(
        responses,
        quality_profile,
        config=PipelineConfig(stages=StageSettings(verification=True, repair=True)),
    )
    page = RenderedPage(source_page=2, png_bytes=b"png", width=10, height=10)

    result = extractor.extract_primary(page, job_id="parse-test", page_count=3)

    assert responses.kwargs["model"] == "gpt-6-sol"
    assert responses.kwargs["store"] is False
    assert responses.kwargs["reasoning"] == {"effort": "medium"}
    assert responses.kwargs["max_output_tokens"] == FULL_PAGE_MAX_OUTPUT_TOKENS == 32_000
    image = responses.kwargs["input"][0]["content"][1]
    assert image["detail"] == "original"
    assert result.usage.cached_input_tokens == 10
    assert result.usage.cache_write_tokens == 5
    assert result.request_id == "req_test"
    assert result.attempts == 1
    assert result.api_call_count == 1
    assert result.retry_count == 0
    schema = responses.kwargs["text_format"].model_json_schema()
    assert '"markdown"' not in str(schema)
    assert '"range"' not in str(schema)


def test_base_url_allows_only_official_openai_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://us.api.openai.com/v1")
    assert resolve_openai_base_url() == "https://us.api.openai.com/v1"

    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    with pytest.raises(RuntimeError, match="official HTTPS OpenAI"):
        resolve_openai_base_url()

    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1/responses")
    with pytest.raises(RuntimeError, match="/v1 API root"):
        resolve_openai_base_url()

    monkeypatch.setenv("OPENAI_BASE_URL", "https://secret@api.openai.com/v1")
    with pytest.raises(RuntimeError, match="must not include credentials"):
        resolve_openai_base_url()

    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com:8443/v1")
    with pytest.raises(RuntimeError, match="non-HTTPS port"):
        resolve_openai_base_url()


def test_responses_parser_uses_dense_page_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    class FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.responses = sentinel

    monkeypatch.setattr("ade_app.openai_client.OpenAI", FakeOpenAI)

    assert build_responses_parser("secret") is sentinel
    assert captured["timeout"] == OPENAI_TIMEOUT_SECONDS == 300.0
    assert captured["max_retries"] == 0


def test_api_key_rejects_header_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="invalid whitespace"):
        resolve_api_key("secret\r\ninjected")


def test_semantic_parse_rejects_missing_audits(
    audited_semantic_page: AuditedSemanticPageExtraction,
) -> None:
    payload = audited_semantic_page.model_dump(mode="json")
    payload["audits"] = []

    with pytest.raises(ValueError, match="cover every segment"):
        AuditedSemanticPageExtraction.model_validate(payload)


def test_semantic_parse_rejects_duplicate_audits(
    audited_semantic_page: AuditedSemanticPageExtraction,
) -> None:
    payload = audited_semantic_page.model_dump(mode="json")
    duplicate = dict(payload["audits"][0])
    duplicate["completeness"] = "missing"
    duplicate["image_agreement"] = "contradicted"
    payload["audits"].append(duplicate)

    with pytest.raises(ValueError, match="cover every segment"):
        AuditedSemanticPageExtraction.model_validate(payload)


def test_invalid_primary_page_retries_without_switching_models(
    audited_semantic_page: AuditedSemanticPageExtraction, quality_profile
) -> None:
    responses = FailOnceResponses(audited_semantic_page)
    result = OpenAIPageExtractor(
        responses,
        quality_profile,
        config=PipelineConfig(stages=StageSettings(verification=True, repair=True)),
    ).extract_primary(
        RenderedPage(source_page=1, png_bytes=b"png", width=10, height=10),
        job_id="parse-test",
        page_count=1,
    )

    assert responses.calls == 2
    assert responses.create_calls == 0
    assert result.attempts == 2
    assert result.api_call_count == 2
    assert result.retry_count == 1
    assert responses.kwargs["model"] == "gpt-6-sol"
    assert responses.kwargs["reasoning"] == {"effort": "medium"}
    assert responses.kwargs["metadata"]["attempt"] == "full-gpt-6-sol"


def test_persistently_invalid_terra_page_stops_after_bounded_retry(
    audited_semantic_page: AuditedSemanticPageExtraction, quality_profile
) -> None:
    responses = AlwaysFailResponses(audited_semantic_page)

    with pytest.raises(StructuredOutputError, match="after 2 attempts"):
        OpenAIPageExtractor(
            responses,
            quality_profile,
            config=PipelineConfig(stages=StageSettings(verification=True, repair=True)),
        ).extract(
            RenderedPage(source_page=1, png_bytes=b"png", width=10, height=10),
            job_id="parse-test",
            page_count=1,
        )

    assert responses.calls == 2


def test_billed_invalid_response_usage_is_retained_on_retry(
    audited_semantic_page: AuditedSemanticPageExtraction, quality_profile
) -> None:
    responses = ReturnedInvalidResponses(audited_semantic_page)

    result = OpenAIPageExtractor(
        responses,
        quality_profile,
        config=PipelineConfig(stages=StageSettings(verification=True, repair=True)),
    ).extract_primary(
        RenderedPage(source_page=1, png_bytes=b"png", width=10, height=10),
        job_id="parse-test",
        page_count=1,
    )

    assert responses.calls == 2
    assert result.attempts == 2
    assert result.usage.input_tokens == 200
    assert result.usage.cached_input_tokens == 20
    assert result.usage.output_tokens == 40


def test_billed_invalid_response_usage_is_attached_to_final_failure(
    audited_semantic_page: AuditedSemanticPageExtraction, quality_profile
) -> None:
    responses = ReturnedInvalidResponses(audited_semantic_page, always=True)

    with pytest.raises(StructuredOutputError) as captured:
        OpenAIPageExtractor(
            responses,
            quality_profile,
            config=PipelineConfig(stages=StageSettings(verification=True, repair=True)),
        ).extract_primary(
            RenderedPage(source_page=1, png_bytes=b"png", width=10, height=10),
            job_id="parse-test",
            page_count=1,
        )

    error = captured.value
    assert error.usage.input_tokens == 200
    assert error.usage_by_model[0][0] == "gpt-6-sol"
    assert error.api_call_count == 2
    assert error.routing_call_count == 0
    assert error.retry_count == 1


def test_semantic_validation_rejects_uncontained_atomic_range(
    sample_page: PageExtraction,
) -> None:
    damaged = sample_page.model_copy(deep=True)
    element = damaged.children[0]
    assert isinstance(element, DraftLeaf)
    atomic = element.atomic_grounding[0].model_copy(deep=True)
    atomic.range.start = element.grounding.range.end
    atomic.range.end = element.grounding.range.end + 1
    element.atomic_grounding[0] = atomic

    with pytest.raises(ValueError, match="contained"):
        validate_page_extraction(damaged)


def test_semantic_validation_rejects_nonempty_ungrounded_page() -> None:
    with pytest.raises(ValueError, match="grounded elements"):
        validate_page_extraction(PageExtraction(markdown="Visible text", children=[]))


def test_missing_sol_patch_retains_terra_and_needs_review(
    audited_semantic_page: AuditedSemanticPageExtraction,
) -> None:
    initial = audited_semantic_page.model_copy(deep=True)
    initial.audits[0] = SegmentAudit(
        segment_index=0,
        completeness="complete",
        image_agreement="contradicted",
        findings=[],
    )

    class CascadeResponses(FakeResponses):
        def parse(self, **kwargs: Any) -> Any:
            self.calls = getattr(self, "calls", 0) + 1
            self.kwargs = kwargs
            parsed = initial if self.calls == 1 else None
            return SimpleNamespace(
                output_parsed=parsed,
                id=f"resp-{self.calls}",
                _request_id=f"req-{self.calls}",
                service_tier="standard",
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )

    profile = QualityProfile(
        feature_names=FEATURE_NAMES,
        coefficients=(0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 0.0),
        intercept=-5.0,
        threshold=90.0,
        prompt_hashes={},
        groundtruth_hashes={},
        validation={},
    )
    png = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(png, format="PNG")
    responses = CascadeResponses(initial)

    result = OpenAIPageExtractor(
        responses,
        profile,
        config=PipelineConfig(stages=StageSettings(verification=True, repair=True)),
    ).extract(RenderedPage(1, png.getvalue(), 100, 100), job_id="parse-test", page_count=1)

    assert responses.calls == 2
    assert responses.kwargs["model"] == "gpt-6-sol"
    assert responses.kwargs["reasoning"] == {"effort": "medium"}
    assert responses.kwargs["max_output_tokens"] == CONSENSUS_MAX_OUTPUT_TOKENS == 8_000
    assert result.models_used == ("gpt-6-sol",)
    assert result.api_call_count == 2
    assert result.routing_call_count == 1
    assert result.retry_count == 0
    assert result.segments[0].status == "needs_review"
    assert "independent_read_failure" in result.segments[0].reasons
    assert [attempt.model for attempt in result.segments[0].attempts] == [
        "gpt-6-sol",
        "gpt-6-sol",
    ]
    assert result.segments[0].attempts[1].batch_index == 1


def test_sol_can_confirm_independent_correction(
    audited_semantic_page: AuditedSemanticPageExtraction,
) -> None:
    initial = audited_semantic_page.model_copy(deep=True)
    initial.audits[0].image_agreement = "contradicted"
    corrected = initial.children[0].model_copy(deep=True)
    assert isinstance(corrected, SemanticLeaf)
    corrected.lines[0].content = [SemanticText(text="Corrected heading")]
    sol = SemanticSegmentPatch(
        segment_id="p1-s0",
        element=corrected,
        audit=SegmentPatchAudit(
            segment_index=0,
            completeness="complete",
            image_agreement="supported",
            findings=[],
        ),
    )

    class RepairResponses(FakeResponses):
        def parse(self, **kwargs: Any) -> Any:
            self.calls = getattr(self, "calls", 0) + 1
            self.kwargs = kwargs
            if self.calls == 1:
                parsed = initial
            elif self.calls == 2:
                parsed = sol
            else:
                parsed = SemanticFieldResolutionBatch(
                    segment_id="p1-s0",
                    resolutions=[
                        FieldResolution(
                            field_id="line-0",
                            kind="line",
                            status="resolved",
                            line=SemanticLineValue(
                                content=[SemanticText(text="Corrected heading")],
                                style="heading_1",
                            ),
                        )
                    ],
                )
            return SimpleNamespace(
                output_parsed=parsed,
                id=f"resp-{self.calls}",
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )

    profile = QualityProfile(
        feature_names=FEATURE_NAMES,
        coefficients=(0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 0.0),
        intercept=-5.0,
        threshold=90.0,
        prompt_hashes={},
        groundtruth_hashes={},
        validation={},
    )
    png = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(png, format="PNG")

    responses = RepairResponses(initial)
    result = OpenAIPageExtractor(
        responses,
        profile,
        config=PipelineConfig(stages=StageSettings(verification=True, repair=True)),
    ).extract(RenderedPage(1, png.getvalue(), 100, 100), job_id="parse-test", page_count=1)

    assert result.extraction.markdown.strip() == "# Corrected heading"
    assert result.segments[0].status == "accepted_resolution"
    assert result.segments[0].unresolved_fields == ()
    assert result.segments[0].final_score < 90
    assert result.models_used == ("gpt-6-sol",)
    assert result.api_call_count == 3
    assert result.routing_call_count == 2
    assert result.retry_count == 0
    assert responses.kwargs["max_output_tokens"] == FIELD_RESOLUTION_MAX_OUTPUT_TOKENS == 4_000


def test_independent_consensus_is_counted_per_failing_segment(
    audited_semantic_page: AuditedSemanticPageExtraction,
) -> None:
    initial = audited_semantic_page.model_copy(deep=True)
    initial.children = [initial.children[0].model_copy(deep=True) for _ in range(2)]
    initial.audits = [
        SegmentAudit(
            segment_index=index,
            completeness="complete",
            image_agreement="contradicted",
            findings=[],
        )
        for index in range(2)
    ]
    patches = [
        SemanticSegmentPatch(
            segment_id=f"p1-s{index}",
            element=initial.children[index],
            audit=SegmentPatchAudit(
                segment_index=0,
                completeness="complete",
                image_agreement="supported",
                findings=[],
            ),
        )
        for index in range(2)
    ]

    class Responses(FakeResponses):
        def parse(self, **kwargs: Any) -> Any:
            self.calls = getattr(self, "calls", 0) + 1
            parsed = initial if self.calls == 1 else patches[self.calls - 2]
            return SimpleNamespace(
                output_parsed=parsed,
                id=f"resp-{self.calls}",
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )

    profile = QualityProfile(
        feature_names=FEATURE_NAMES,
        coefficients=(0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 0.0),
        intercept=-5.0,
        threshold=90.0,
        prompt_hashes={},
        groundtruth_hashes={},
        validation={},
    )
    png = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(png, format="PNG")

    result = OpenAIPageExtractor(
        Responses(initial),
        profile,
        config=PipelineConfig(stages=StageSettings(verification=True, repair=True)),
    ).extract(RenderedPage(1, png.getvalue(), 100, 100), job_id="parse-test", page_count=1)

    assert result.attempts == 3
    assert result.api_call_count == 3
    assert result.routing_call_count == 2
    assert result.retry_count == 0
    assert result.segments[0].status == "accepted_consensus"
    assert result.segments[0].final_score < 90
    assert result.segments[1].status == "accepted_consensus"
    assert all(len(segment.attempts) == 2 for segment in result.segments)


def test_independent_consensus_reads_each_low_quality_segment(
    audited_semantic_page: AuditedSemanticPageExtraction,
) -> None:
    initial = audited_semantic_page.model_copy(deep=True)
    initial.audits[0].image_agreement = "contradicted"
    initial.children = [initial.children[0].model_copy(deep=True) for _ in range(5)]
    initial.audits = [
        SegmentAudit(
            segment_index=index,
            completeness="complete",
            image_agreement="contradicted",
            findings=[],
        )
        for index in range(5)
    ]

    class BatchedResponses(FakeResponses):
        batch_sizes: list[int]

        def __init__(self, extraction: AuditedSemanticPageExtraction) -> None:
            super().__init__(extraction)
            self.batch_sizes = []

        def parse(self, **kwargs: Any) -> Any:
            self.calls = getattr(self, "calls", 0) + 1
            self.kwargs = kwargs
            if self.calls == 1:
                parsed = initial
            else:
                segment_id = kwargs["input"][0]["content"][0]["text"].split("=", 1)[1].rstrip(".")
                index = int(segment_id.rsplit("s", 1)[1])
                parsed = SemanticSegmentPatch(
                    segment_id=segment_id,
                    element=initial.children[index],
                    audit=SegmentPatchAudit(
                        segment_index=0,
                        completeness="complete",
                        image_agreement="supported",
                        findings=[],
                    ),
                )
            return SimpleNamespace(
                output_parsed=parsed,
                id=f"resp-{self.calls}",
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )

    profile = QualityProfile(
        feature_names=FEATURE_NAMES,
        coefficients=(0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 0.0),
        intercept=-5.0,
        threshold=90.0,
        prompt_hashes={},
        groundtruth_hashes={},
        validation={},
    )
    png = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(png, format="PNG")

    responses = BatchedResponses(initial)
    result = OpenAIPageExtractor(
        responses,
        profile,
        config=PipelineConfig(stages=StageSettings(verification=True, repair=True)),
    ).extract(RenderedPage(1, png.getvalue(), 100, 100), job_id="parse-test", page_count=1)

    assert responses.calls == 6
    assert result.attempts == 6
    assert all(segment.status == "accepted_consensus" for segment in result.segments), [
        (
            segment.segment_id,
            segment.status,
            segment.final_score,
            segment.reasons,
            segment.attempts[-1].failure_reason,
        )
        for segment in result.segments
    ]
    assert {segment.attempts[1].batch_index for segment in result.segments} == {1, 2, 3, 4, 5}
    assert result.models_used == ("gpt-6-sol",)


def test_matching_but_uncertain_independent_read_remains_needs_review(
    audited_semantic_page: AuditedSemanticPageExtraction,
) -> None:
    initial = audited_semantic_page.model_copy(deep=True)
    initial.audits[0].image_agreement = "contradicted"
    patch = SemanticSegmentPatch(
        segment_id="p1-s0",
        element=initial.children[0],
        audit=SegmentPatchAudit(
            segment_index=0,
            completeness="complete",
            image_agreement="contradicted",
            findings=[],
        ),
    )

    class Responses(FakeResponses):
        def parse(self, **kwargs: Any) -> Any:
            self.calls = getattr(self, "calls", 0) + 1
            return SimpleNamespace(
                output_parsed=initial if self.calls == 1 else patch,
                id=f"resp-{self.calls}",
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )

    profile = QualityProfile(
        feature_names=FEATURE_NAMES,
        coefficients=(0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 0.0),
        intercept=-5.0,
        threshold=90.0,
        prompt_hashes={},
        groundtruth_hashes={},
        validation={},
    )
    png = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(png, format="PNG")

    result = OpenAIPageExtractor(
        Responses(initial),
        profile,
        config=PipelineConfig(stages=StageSettings(verification=True, repair=True)),
    ).extract(RenderedPage(1, png.getvalue(), 100, 100), job_id="parse-test", page_count=1)

    assert result.attempts == 2
    assert result.segments[0].status == "needs_review"
    assert result.segments[0].final_score < 90
    assert "both_reads_uncertain" in result.segments[0].reasons
    assert "independent:image_agreement:contradicted" in result.segments[0].reasons


def test_independent_transport_retry_is_explicitly_counted(
    audited_semantic_page: AuditedSemanticPageExtraction,
) -> None:
    initial = audited_semantic_page.model_copy(deep=True)
    initial.audits[0].image_agreement = "contradicted"
    patch = SemanticSegmentPatch(
        segment_id="p1-s0",
        element=initial.children[0],
        audit=SegmentPatchAudit(
            segment_index=0,
            completeness="complete",
            image_agreement="supported",
            findings=[],
        ),
    )

    class Responses(FakeResponses):
        def parse(self, **kwargs: Any) -> Any:
            self.calls = getattr(self, "calls", 0) + 1
            if self.calls == 2:
                raise APIConnectionError(
                    request=httpx.Request("POST", "https://api.openai.com/v1/responses")
                )
            return SimpleNamespace(
                output_parsed=initial if self.calls == 1 else patch,
                id=f"resp-{self.calls}",
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )

    profile = QualityProfile(
        feature_names=FEATURE_NAMES,
        coefficients=(0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 0.0),
        intercept=-5.0,
        threshold=90.0,
        prompt_hashes={},
        groundtruth_hashes={},
        validation={},
    )
    png = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(png, format="PNG")
    responses = Responses(initial)

    result = OpenAIPageExtractor(
        responses,
        profile,
        config=PipelineConfig(stages=StageSettings(verification=True, repair=True)),
    ).extract(RenderedPage(1, png.getvalue(), 100, 100), job_id="parse-test", page_count=1)

    assert responses.calls == 3
    assert result.attempts == 3
    assert result.api_call_count == 3
    assert result.routing_call_count == 2
    assert result.retry_count == 1
    assert result.segments[0].attempts[1].api_call_count == 2
    assert result.segments[0].attempts[1].retry_count == 1
    assert result.segments[0].status == "accepted_consensus"
    assert MAX_CONCURRENT_RESPONSES == 4


def test_escalation_budget_marks_remaining_segments_for_review(
    audited_semantic_page: AuditedSemanticPageExtraction,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ade_app.config import PipelineConfig

    config = PipelineConfig(stages=StageSettings(verification=True, repair=True))
    config.routing.max_escalated_segments_per_page = 1
    initial = audited_semantic_page.model_copy(deep=True)
    initial.children = [initial.children[0].model_copy(deep=True) for _ in range(2)]
    initial.audits = [
        SegmentAudit(
            segment_index=index,
            completeness="complete",
            image_agreement="contradicted",
            findings=[],
        )
        for index in range(2)
    ]

    class Responses(FakeResponses):
        def parse(self, **kwargs: Any) -> Any:
            self.calls = getattr(self, "calls", 0) + 1
            self.kwargs = kwargs
            parsed = initial
            if self.calls == 2:
                parsed = SemanticSegmentPatch(
                    segment_id="p1-s0",
                    element=initial.children[0],
                    audit=SegmentPatchAudit(
                        segment_index=0,
                        completeness="complete",
                        image_agreement="supported",
                        findings=[],
                    ),
                )
            return SimpleNamespace(
                output_parsed=parsed,
                id=f"resp-{self.calls}",
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )

    profile = QualityProfile(
        feature_names=FEATURE_NAMES,
        coefficients=(0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 0.0),
        intercept=-5.0,
        threshold=90.0,
        prompt_hashes={},
        groundtruth_hashes={},
        validation={},
    )
    png = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(png, format="PNG")
    responses = Responses(initial)

    result = OpenAIPageExtractor(responses, profile, config=config).extract(
        RenderedPage(1, png.getvalue(), 100, 100), job_id="parse-test", page_count=1
    )

    assert responses.calls == 2
    assert result.segments[0].status == "accepted_consensus"
    assert result.segments[1].status == "needs_review"
    assert "repair_budget_exhausted" in result.segments[1].reasons


def test_unconfirmed_field_is_redacted_without_calling_sol(
    audited_semantic_page: AuditedSemanticPageExtraction,
    quality_profile: QualityProfile,
) -> None:
    initial = audited_semantic_page.model_copy(deep=True)
    assert isinstance(initial.children[0], SemanticLeaf)
    initial.children[0].lines[0].content = [SemanticText(text="Name: Original Name")]
    initial.children[0].lines[0].style = "plain"
    independent = initial.children[0].model_copy(deep=True)
    independent.lines[0].content = [SemanticText(text="Name: Invented Name")]

    class Responses(FakeResponses):
        def parse(self, **kwargs: Any) -> Any:
            self.calls = getattr(self, "calls", 0) + 1
            self.kwargs = kwargs
            parsed = initial
            if self.calls == 2:
                parsed = SemanticSegmentPatch(
                    segment_id="p1-s0",
                    element=independent,
                    audit=SegmentPatchAudit(
                        segment_index=0,
                        completeness="complete",
                        image_agreement="supported",
                        findings=[],
                    ),
                )
            return SimpleNamespace(
                output_parsed=parsed,
                id=f"resp-{self.calls}",
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )

    png = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(png, format="PNG")
    responses = Responses(initial)
    extractor = OpenAIPageExtractor(
        responses,
        quality_profile,
        config=PipelineConfig(stages=StageSettings(verification=True, repair=True)),
    )
    page = RenderedPage(1, png.getvalue(), 100, 100)

    primary = extractor.extract_primary(page, job_id="parse-test", page_count=1)
    result = extractor.finalize(page, primary, job_id="parse-test", allow_repair=False)

    assert responses.calls == 2
    assert responses.kwargs["model"] == "gpt-6-sol"
    assert "[UNVERIFIED]" in result.extraction.markdown
    assert "Original Name" not in result.extraction.markdown
    assert "Invented Name" not in result.extraction.markdown
    assert result.segments[0].status == "needs_review"
    record = SimpleNamespace(source_page=1, segments=result.segments)
    candidates = discover_raw_fields((PageOutcome(1, result.extraction),), (record,))
    assert len(candidates) == 1
    assert candidates[0].value == "[UNVERIFIED]"
