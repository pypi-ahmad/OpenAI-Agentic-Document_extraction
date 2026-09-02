"""Narrow OpenAI Responses API wrapper for page extraction."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any
from urllib.parse import urlparse

from openai import OpenAI, OpenAIError
from PIL import Image, ImageStat

from ade_app.consensus import PeerEvidence, apply_resolutions, compare_elements
from ade_app.constants import (
    MODEL_CASCADE,
    OPENAI_BASE_URL,
    PRIMARY_MODEL,
    QUALITY_PROFILE_PATH,
    QUALITY_THRESHOLD,
    REPAIR_MODEL,
)
from ade_app.cost import TokenUsage
from ade_app.models import (
    AuditedPageExtraction,
    AuditedSemanticPageExtraction,
    DraftTable,
    PageExtraction,
    SemanticFieldResolutionBatch,
    SemanticPageExtraction,
    SemanticSegmentPatch,
)
from ade_app.quality import (
    QualityProfile,
    load_quality_profile,
    measure_segment,
    segment_features,
)
from ade_app.raster import RenderedPage, crop_segment, transform_semantic_element_from_crop
from ade_app.rendering import render_semantic_page

PROMPT_PATH = Path(__file__).with_name("prompts") / "page_extraction.md"
CONSENSUS_PROMPT_PATH = PROMPT_PATH.with_name("segment_consensus.md")
FIELD_RESOLUTION_PROMPT_PATH = PROMPT_PATH.with_name("field_resolution.md")
OPENAI_TIMEOUT_SECONDS = 300.0
FULL_PAGE_MAX_OUTPUT_TOKENS = 32_000
MAX_CONCURRENT_RESPONSES = 4
_RESPONSES_SEMAPHORE = BoundedSemaphore(MAX_CONCURRENT_RESPONSES)


def active_prompt_hashes() -> dict[str, str]:
    """Return non-content identifiers for the prompts active in production extraction."""

    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (PROMPT_PATH, CONSENSUS_PROMPT_PATH, FIELD_RESOLUTION_PROMPT_PATH)
    }


FULL_PAGE_MAX_ATTEMPTS = 2
ROUTING_MAX_ATTEMPTS = 2
CONSENSUS_MAX_OUTPUT_TOKENS = 8_000
FIELD_RESOLUTION_MAX_OUTPUT_TOKENS = 4_000
MAX_ESCALATED_SEGMENTS_PER_PAGE = 16


@dataclass(frozen=True, slots=True)
class SegmentAttempt:
    model: str
    effort: str
    score: float
    accepted: bool
    usage: TokenUsage
    response_id: str = ""
    request_id: str = ""
    failure_reason: str | None = None
    stage: str = "primary"
    peer_source_page: int | None = None
    disagreement_count: int = 0
    batch_index: int | None = None
    api_call_count: int = 1
    retry_count: int = 0


@dataclass(frozen=True, slots=True)
class SegmentRecord:
    segment_id: str
    segment_index: int
    final_score: float
    status: str
    reasons: tuple[str, ...]
    attempts: tuple[SegmentAttempt, ...]
    structural_conflicts: tuple[str, ...] = ()
    unresolved_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PageResponse:
    extraction: PageExtraction
    usage: TokenUsage
    response_id: str
    request_id: str
    service_tier: str
    range_repairs: int
    attempts: int = 1
    usage_by_model: tuple[tuple[str, TokenUsage], ...] = ()
    segments: tuple[SegmentRecord, ...] = ()
    models_used: tuple[str, ...] = ()
    api_call_count: int = 1
    routing_call_count: int = 0
    retry_count: int = 0


@dataclass(frozen=True, slots=True)
class _ExtractionState:
    semantic: AuditedSemanticPageExtraction
    rendered: AuditedPageExtraction


@dataclass(frozen=True, slots=True)
class PrimaryPageResponse:
    state: _ExtractionState
    usage: TokenUsage
    response_id: str
    request_id: str
    service_tier: str
    attempts: int


class StructuredOutputError(ValueError):
    def __init__(
        self,
        attempts: int,
        usage_by_model: tuple[tuple[str, TokenUsage], ...] = (),
    ) -> None:
        super().__init__(f"structured output invalid after {attempts} attempts")
        self.attempts = attempts
        self.api_call_count = attempts
        self.routing_call_count = 0
        self.retry_count = max(attempts - 1, 0)
        self.usage_by_model = usage_by_model
        self.usage = sum((usage for _, usage in usage_by_model), TokenUsage())


class _ResponseValidationError(ValueError):
    """A billed response that failed local structured-output validation."""

    def __init__(
        self,
        message: str,
        usage: TokenUsage,
        *,
        attempts: int = 1,
        retry_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.usage = usage
        self.attempts = attempts
        self.retry_count = retry_count


class _TransportRetryError(ValueError):
    """Transport failure after explicit, countable retry attempts."""

    def __init__(self, attempts: int) -> None:
        super().__init__(f"OpenAI transport failed after {attempts} attempts")
        self.attempts = attempts
        self.retry_count = max(attempts - 1, 0)
        self.usage = TokenUsage()


def resolve_api_key(streamlit_secret: str | None = None) -> str:
    """Resolve the configured key without persisting or logging it."""

    api_key = os.environ.get("OPENAI_API_KEY") or streamlit_secret
    if not api_key:
        raise RuntimeError("Required credential OPENAI_API_KEY is unavailable")
    if api_key != api_key.strip() or any(ord(character) < 32 for character in api_key):
        raise RuntimeError("OPENAI_API_KEY contains invalid whitespace or control characters")
    return api_key


def build_responses_parser(api_key: str) -> Any:
    """Build an official OpenAI client, including an official regional endpoint."""

    return OpenAI(
        api_key=api_key,
        base_url=resolve_openai_base_url(),
        max_retries=0,
        timeout=OPENAI_TIMEOUT_SECONDS,
    ).responses


def resolve_openai_base_url() -> str:
    """Allow the global endpoint or a configured official regional OpenAI endpoint."""

    configured = os.environ.get("OPENAI_BASE_URL")
    if not configured:
        return OPENAI_BASE_URL
    parsed = urlparse(configured)
    hostname = (parsed.hostname or "").lower()
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise RuntimeError("OPENAI_BASE_URL must not include credentials or a non-HTTPS port")
    if parsed.scheme != "https" or not (
        hostname == "api.openai.com" or hostname.endswith(".api.openai.com")
    ):
        raise RuntimeError("OPENAI_BASE_URL must use an official HTTPS OpenAI API hostname")
    if parsed.path.rstrip("/") not in {"", "/v1"} or parsed.query or parsed.fragment:
        raise RuntimeError("OPENAI_BASE_URL must target the OpenAI /v1 API root")
    root = configured.rstrip("/")
    return root if parsed.path.rstrip("/") == "/v1" else f"{root}/v1"


class OpenAIPageExtractor:
    """Extract and quality-route one rendered page through the Responses API."""

    def __init__(
        self,
        responses: Any,
        profile: QualityProfile | None = None,
        *,
        profile_path: str | Path | None = QUALITY_PROFILE_PATH,
    ) -> None:
        self._responses = responses
        self._profile = profile or (
            load_quality_profile(profile_path) if profile_path is not None else None
        )
        self._instructions = PROMPT_PATH.read_text(encoding="utf-8")
        self._consensus_instructions = CONSENSUS_PROMPT_PATH.read_text(encoding="utf-8")
        self._field_resolution_instructions = FIELD_RESOLUTION_PROMPT_PATH.read_text(
            encoding="utf-8"
        )
        active_hashes = active_prompt_hashes()
        current_hashes = {PROMPT_PATH.name: active_hashes[PROMPT_PATH.name]}
        if (
            self._profile is not None
            and self._profile.prompt_hashes
            and self._profile.prompt_hashes != current_hashes
        ):
            raise RuntimeError("Calibrated quality profile does not match the active prompts")
        if (
            self._profile is not None
            and self._profile.profile_version >= 3
            and (self._profile.model_id, self._profile.reasoning_effort) != PRIMARY_MODEL
        ):
            raise RuntimeError("Calibrated quality profile does not match the primary model")

    def extract(self, page: RenderedPage, *, job_id: str, page_count: int) -> PageResponse:
        """Extract one page; document pipelines should use the staged methods for peer evidence."""

        primary = self.extract_primary(page, job_id=job_id, page_count=page_count)
        return self.finalize(page, primary, job_id=job_id)

    def extract_primary(
        self, page: RenderedPage, *, job_id: str, page_count: int
    ) -> PrimaryPageResponse:
        if self._profile is None:
            raise RuntimeError("A calibrated quality profile is required for Terra extraction")
        attempts = 0
        failed_usage = TokenUsage()
        state: _ExtractionState | None = None
        result: tuple[_ExtractionState, TokenUsage, str, str, str, int] | None = None
        for _ in range(FULL_PAGE_MAX_ATTEMPTS):
            attempts += 1
            try:
                result = self._extract_once(
                    page,
                    job_id=job_id,
                    page_count=page_count,
                    model=PRIMARY_MODEL[0],
                    effort=PRIMARY_MODEL[1],
                )
                state = result[0]
                break
            except _ResponseValidationError as error:
                failed_usage += error.usage
                continue
            except OpenAIError, ValueError:
                continue
        if state is None:
            raise StructuredOutputError(attempts, ((PRIMARY_MODEL[0], failed_usage),))
        if result is None:
            raise StructuredOutputError(attempts, ((PRIMARY_MODEL[0], failed_usage),))
        return PrimaryPageResponse(
            state,
            failed_usage + result[1],
            result[2],
            result[3],
            result[4],
            attempts,
        )

    def finalize(
        self,
        page: RenderedPage,
        primary: PrimaryPageResponse,
        *,
        job_id: str,
        peers: dict[str, PeerEvidence] | None = None,
    ) -> PageResponse:
        """Independently reread low-quality segments and resolve only disputed fields."""

        if self._profile is None:
            raise RuntimeError("quality profile is unavailable")
        peers = peers or {}
        state = primary.state
        usage_by_model = {PRIMARY_MODEL[0]: primary.usage}
        service_tier = primary.service_tier
        api_call_count = primary.attempts
        routing_call_count = 0
        retry_count = max(primary.attempts - 1, 0)
        scores: dict[int, float] = {}
        reasons: dict[int, tuple[str, ...]] = {}
        statuses: dict[int, str] = {}
        segment_attempts: dict[int, list[SegmentAttempt]] = {}
        unresolved_by_index: dict[int, tuple[str, ...]] = {}
        failing: list[int] = []

        for index in range(len(state.semantic.children)):
            quality = measure_segment(
                state.rendered, index, state.semantic.audits[index], self._profile
            )
            scores[index] = quality.score
            reasons[index] = quality.reasons
            statuses[index] = (
                "accepted_quality" if quality.score >= QUALITY_THRESHOLD else "needs_review"
            )
            segment_attempts[index] = [
                SegmentAttempt(
                    PRIMARY_MODEL[0],
                    PRIMARY_MODEL[1],
                    quality.score,
                    True,
                    TokenUsage(),
                    primary.response_id,
                    primary.request_id,
                    api_call_count=primary.attempts,
                    retry_count=max(primary.attempts - 1, 0),
                )
            ]
            if quality.score < QUALITY_THRESHOLD:
                failing.append(index)

        for index in failing[MAX_ESCALATED_SEGMENTS_PER_PAGE:]:
            reasons[index] = tuple((*reasons[index], "repair_budget_exhausted"))

        for batch_index, index in enumerate(failing[:MAX_ESCALATED_SEGMENTS_PER_PAGE], 1):
            segment_id = f"p{page.source_page}-s{index}"
            try:
                independent, usage, response_id, request_id, tier, call_count = (
                    self._independent_read(
                        page,
                        state,
                        index,
                        job_id=job_id,
                        batch_index=batch_index,
                        peer=peers.get(segment_id),
                    )
                )
            except (OpenAIError, ValueError) as error:
                call_count = int(getattr(error, "attempts", 1))
                call_retries = int(getattr(error, "retry_count", max(call_count - 1, 0)))
                api_call_count += call_count
                routing_call_count += call_count
                retry_count += call_retries
                failed_usage = getattr(error, "usage", TokenUsage())
                usage_by_model[PRIMARY_MODEL[0]] = (
                    usage_by_model.get(PRIMARY_MODEL[0], TokenUsage()) + failed_usage
                )
                segment_attempts[index].append(
                    SegmentAttempt(
                        PRIMARY_MODEL[0],
                        PRIMARY_MODEL[1],
                        scores[index],
                        False,
                        failed_usage,
                        failure_reason=f"{type(error).__name__}: independent read failed",
                        stage="independent_consensus",
                        batch_index=batch_index,
                        api_call_count=call_count,
                        retry_count=call_retries,
                    )
                )
                reasons[index] = tuple((*reasons[index], "independent_read_failure"))
                continue

            api_call_count += call_count
            routing_call_count += call_count
            retry_count += max(call_count - 1, 0)
            usage_by_model[PRIMARY_MODEL[0]] = (
                usage_by_model.get(PRIMARY_MODEL[0], TokenUsage()) + usage
            )
            if tier == "priority":
                service_tier = "priority"
            comparison = compare_elements(state.semantic.children[index], independent.element)
            independent_score, independent_reasons = _measure_patch(independent, self._profile)
            both_reads_uncertain = bool(reasons[index]) and bool(independent_reasons)
            segment_attempts[index].append(
                SegmentAttempt(
                    PRIMARY_MODEL[0],
                    PRIMARY_MODEL[1],
                    independent_score,
                    not comparison.structural_conflicts
                    and not comparison.disagreements
                    and not both_reads_uncertain,
                    usage,
                    response_id,
                    request_id,
                    stage="independent_consensus",
                    peer_source_page=(
                        peers[segment_id].source_page if segment_id in peers else None
                    ),
                    disagreement_count=len(comparison.disagreements),
                    batch_index=batch_index,
                    api_call_count=call_count,
                    retry_count=max(call_count - 1, 0),
                )
            )
            reasons[index] = tuple(
                (*reasons[index], *(f"independent:{reason}" for reason in independent_reasons))
            )
            if comparison.structural_conflicts:
                reasons[index] = tuple((*reasons[index], *comparison.structural_conflicts))
                continue
            if both_reads_uncertain:
                reasons[index] = tuple((*reasons[index], "both_reads_uncertain"))
                continue
            if not comparison.disagreements:
                statuses[index] = "accepted_consensus"
                continue
            try:
                (
                    resolutions,
                    sol_usage,
                    sol_response_id,
                    sol_request_id,
                    sol_tier,
                    sol_call_count,
                ) = self._resolve_fields(
                    page,
                    segment_id,
                    comparison.disagreements,
                    job_id=job_id,
                    batch_index=batch_index,
                )
                resolved, unresolved = apply_resolutions(
                    state.semantic.children[index],
                    comparison.disagreements,
                    resolutions.resolutions,
                )
                candidate = _replace_element(state, index, resolved)
            except (OpenAIError, ValueError) as error:
                sol_call_count = int(getattr(error, "attempts", 1))
                sol_retries = int(getattr(error, "retry_count", max(sol_call_count - 1, 0)))
                api_call_count += sol_call_count
                routing_call_count += sol_call_count
                retry_count += sol_retries
                failed_usage = getattr(error, "usage", TokenUsage())
                usage_by_model[REPAIR_MODEL[0]] = (
                    usage_by_model.get(REPAIR_MODEL[0], TokenUsage()) + failed_usage
                )
                segment_attempts[index].append(
                    SegmentAttempt(
                        REPAIR_MODEL[0],
                        REPAIR_MODEL[1],
                        scores[index],
                        False,
                        failed_usage,
                        failure_reason=f"{type(error).__name__}: field resolution failed",
                        stage="field_resolution",
                        disagreement_count=len(comparison.disagreements),
                        batch_index=batch_index,
                        api_call_count=sol_call_count,
                        retry_count=sol_retries,
                    )
                )
                reasons[index] = tuple((*reasons[index], "field_resolution_failure"))
                continue
            api_call_count += sol_call_count
            routing_call_count += sol_call_count
            retry_count += max(sol_call_count - 1, 0)
            usage_by_model[REPAIR_MODEL[0]] = (
                usage_by_model.get(REPAIR_MODEL[0], TokenUsage()) + sol_usage
            )
            if sol_tier == "priority":
                service_tier = "priority"
            accepted = not unresolved
            segment_attempts[index].append(
                SegmentAttempt(
                    REPAIR_MODEL[0],
                    REPAIR_MODEL[1],
                    scores[index],
                    accepted,
                    sol_usage,
                    sol_response_id,
                    sol_request_id,
                    stage="field_resolution",
                    disagreement_count=len(comparison.disagreements),
                    batch_index=batch_index,
                    api_call_count=sol_call_count,
                    retry_count=max(sol_call_count - 1, 0),
                )
            )
            if accepted:
                state = candidate
                statuses[index] = "accepted_resolution"
            else:
                unresolved_by_index[index] = tuple(unresolved)
                reasons[index] = tuple((*reasons[index], "unresolved_fields"))

        records = [
            SegmentRecord(
                f"p{page.source_page}-s{index}",
                index,
                scores[index],
                statuses[index],
                tuple(dict.fromkeys(reasons[index])),
                tuple(segment_attempts[index]),
                structural_conflicts=tuple(
                    reason
                    for reason in reasons[index]
                    if reason in {"segment_type", "segment_topology", "field_set"}
                ),
                unresolved_fields=unresolved_by_index.get(index, ()),
            )
            for index in range(len(state.semantic.children))
        ]

        total_usage = TokenUsage()
        for usage in usage_by_model.values():
            total_usage += usage
        models_used = tuple(model for model, _ in MODEL_CASCADE if model in usage_by_model)
        return PageResponse(
            extraction=PageExtraction(
                markdown=state.rendered.markdown,
                children=state.rendered.children,
            ),
            usage=total_usage,
            response_id=primary.response_id,
            request_id=primary.request_id,
            service_tier=service_tier,
            range_repairs=0,
            attempts=api_call_count,
            usage_by_model=tuple((model, usage_by_model[model]) for model in models_used),
            segments=tuple(records),
            models_used=models_used,
            api_call_count=api_call_count,
            routing_call_count=routing_call_count,
            retry_count=retry_count,
        )

    def extract_for_calibration(
        self, page: RenderedPage, *, job_id: str, page_count: int
    ) -> tuple[AuditedPageExtraction, TokenUsage]:
        """Run only the fixed Terra/medium first stage for calibration."""

        result = self._extract_once(
            page,
            job_id=job_id,
            page_count=page_count,
            model=PRIMARY_MODEL[0],
            effort=PRIMARY_MODEL[1],
        )
        return result[0].rendered, result[1]

    def _extract_once(
        self,
        page: RenderedPage,
        *,
        job_id: str,
        page_count: int,
        model: str,
        effort: str,
    ) -> tuple[_ExtractionState, TokenUsage, str, str, str, int]:
        encoded_image = base64.b64encode(page.png_bytes).decode("ascii")
        response = _parse_once(
            self._responses,
            model=model,
            instructions=self._instructions,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"Extract source page {page.source_page} of {page_count}. "
                                "Return only the structured page result and segment audits."
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{encoded_image}",
                            "detail": "original",
                        },
                    ],
                }
            ],
            text_format=AuditedSemanticPageExtraction,
            reasoning={"effort": effort},
            max_output_tokens=FULL_PAGE_MAX_OUTPUT_TOKENS,
            metadata={
                "app": "openai-ade",
                "job_id": job_id,
                "source_page": str(page.source_page),
                "attempt": f"full-{model}",
            },
            store=False,
        )
        usage = _read_usage(response)
        try:
            parsed_extraction = response.output_parsed
            if parsed_extraction is None:
                raise ValueError("OpenAI response did not contain a parsed page")
            if not parsed_extraction.children and _page_has_visible_ink(page):
                raise ValueError("model returned an empty extraction for a nonblank page")
            rendered_page = render_semantic_page(
                SemanticPageExtraction(children=parsed_extraction.children)
            )
            rendered = AuditedPageExtraction(
                markdown=rendered_page.markdown,
                children=rendered_page.children,
                audits=parsed_extraction.audits,
            )
            validate_page_extraction(rendered)
        except ValueError as error:
            raise _ResponseValidationError(str(error), usage) from error
        response_id = str(getattr(response, "id", ""))
        request_id = str(getattr(response, "_request_id", "") or response_id)
        service_tier = str(getattr(response, "service_tier", "") or "standard")
        return (
            _ExtractionState(parsed_extraction, rendered),
            usage,
            response_id,
            request_id,
            service_tier,
            0,
        )

    def _independent_read(
        self,
        page: RenderedPage,
        state: _ExtractionState,
        index: int,
        *,
        job_id: str,
        batch_index: int,
        peer: PeerEvidence | None,
    ) -> tuple[SemanticSegmentPatch, TokenUsage, str, str, str, int]:
        """Reread one low-quality segment without exposing the primary transcription."""

        segment_id = f"p{page.source_page}-s{index}"
        crop = crop_segment(page, state.semantic.children[index].box)
        content: list[dict[str, str]] = [
            {"type": "input_text", "text": f"Return segment_id={segment_id}."},
            {
                "type": "input_image",
                "image_url": "data:image/png;base64,"
                + base64.b64encode(crop.png_bytes).decode("ascii"),
                "detail": "original",
            },
        ]
        if peer is not None:
            content.extend(
                [
                    {
                        "type": "input_text",
                        "text": f"Masked peer evidence from source page {peer.source_page}.",
                    },
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,"
                        + base64.b64encode(peer.png_bytes).decode("ascii"),
                        "detail": "original",
                    },
                ]
            )
        response, call_count = _parse_with_transport_retry(
            self._responses,
            model=PRIMARY_MODEL[0],
            instructions=self._consensus_instructions,
            input=[{"role": "user", "content": content}],
            text_format=SemanticSegmentPatch,
            reasoning={"effort": PRIMARY_MODEL[1]},
            max_output_tokens=CONSENSUS_MAX_OUTPUT_TOKENS,
            metadata={
                "app": "openai-ade",
                "job_id": job_id,
                "source_page": str(page.source_page),
                "batch_index": str(batch_index),
                "attempt": "independent-consensus",
            },
            store=False,
        )
        usage = _read_usage(response)
        try:
            patch = response.output_parsed
            if not isinstance(patch, SemanticSegmentPatch) or patch.segment_id != segment_id:
                raise ValueError("independent read returned an invalid segment ID")
            patch = patch.model_copy(deep=True)
            patch.element = transform_semantic_element_from_crop(patch.element, crop)
        except ValueError as error:
            raise _ResponseValidationError(
                str(error),
                usage,
                attempts=call_count,
                retry_count=max(call_count - 1, 0),
            ) from error
        return (
            patch,
            usage,
            str(getattr(response, "id", "")),
            str(getattr(response, "_request_id", "") or getattr(response, "id", "")),
            str(getattr(response, "service_tier", "") or "standard"),
            call_count,
        )

    def _resolve_fields(
        self,
        page: RenderedPage,
        segment_id: str,
        disagreements: tuple[Any, ...],
        *,
        job_id: str,
        batch_index: int,
    ) -> tuple[SemanticFieldResolutionBatch, TokenUsage, str, str, str, int]:
        """Ask Sol to resolve only independently-disputed fields from their image regions."""

        content: list[dict[str, str]] = []
        for disagreement in disagreements:
            crop = crop_segment(page, disagreement.primary.box)
            content.extend(
                [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {
                                "segment_id": segment_id,
                                "field_id": disagreement.field_id,
                                "kind": disagreement.kind,
                                "candidate_a": _field_json(disagreement.primary.value),
                                "candidate_b": _field_json(disagreement.independent.value),
                            },
                            ensure_ascii=False,
                        ),
                    },
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,"
                        + base64.b64encode(crop.png_bytes).decode("ascii"),
                        "detail": "original",
                    },
                ]
            )
        response, call_count = _parse_with_transport_retry(
            self._responses,
            model=REPAIR_MODEL[0],
            instructions=self._field_resolution_instructions,
            input=[{"role": "user", "content": content}],
            text_format=SemanticFieldResolutionBatch,
            reasoning={"effort": REPAIR_MODEL[1]},
            max_output_tokens=FIELD_RESOLUTION_MAX_OUTPUT_TOKENS,
            metadata={
                "app": "openai-ade",
                "job_id": job_id,
                "source_page": str(page.source_page),
                "batch_index": str(batch_index),
                "attempt": "field-resolution",
            },
            store=False,
        )
        usage = _read_usage(response)
        try:
            result = response.output_parsed
            if not isinstance(result, SemanticFieldResolutionBatch):
                raise ValueError("field resolution did not return a parsed result")
            if result.segment_id != segment_id:
                raise ValueError("field resolution returned an invalid segment ID")
            expected = {item.field_id for item in disagreements}
            received = {item.field_id for item in result.resolutions}
            if not received <= expected:
                raise ValueError("field resolution returned an unexpected field ID")
        except ValueError as error:
            raise _ResponseValidationError(
                str(error),
                usage,
                attempts=call_count,
                retry_count=max(call_count - 1, 0),
            ) from error
        return (
            result,
            usage,
            str(getattr(response, "id", "")),
            str(getattr(response, "_request_id", "") or getattr(response, "id", "")),
            str(getattr(response, "service_tier", "") or "standard"),
            call_count,
        )


def _replace_element(state: _ExtractionState, index: int, element: Any) -> _ExtractionState:
    semantic = state.semantic.model_copy(deep=True)
    semantic.children[index] = element
    semantic = AuditedSemanticPageExtraction.model_validate(semantic.model_dump())
    page = render_semantic_page(SemanticPageExtraction(children=semantic.children))
    rendered = AuditedPageExtraction(
        markdown=page.markdown,
        children=page.children,
        audits=semantic.audits,
    )
    validate_page_extraction(rendered)
    return _ExtractionState(semantic, rendered)


def _measure_patch(
    patch: SemanticSegmentPatch, profile: QualityProfile
) -> tuple[float, tuple[str, ...]]:
    rendered = render_semantic_page(SemanticPageExtraction(children=[patch.element]))
    audit = patch.audit.model_copy(update={"segment_index": 0})
    features, reasons = segment_features(rendered, 0, audit)
    return profile.score(features), reasons


def _field_json(value: Any) -> Any:
    if isinstance(value, list):
        return [item.model_dump(mode="json") for item in value]
    return value.model_dump(mode="json")


def _parse_once(responses: Any, **kwargs: Any) -> Any:
    with _RESPONSES_SEMAPHORE:
        return responses.parse(**kwargs)


def _parse_with_transport_retry(responses: Any, **kwargs: Any) -> tuple[Any, int]:
    attempts = 0
    while attempts < ROUTING_MAX_ATTEMPTS:
        attempts += 1
        try:
            return _parse_once(responses, **kwargs), attempts
        except OpenAIError as error:
            if attempts == ROUTING_MAX_ATTEMPTS:
                raise _TransportRetryError(attempts) from error
    raise RuntimeError("unreachable transport retry state")


def _page_has_visible_ink(page: RenderedPage) -> bool:
    try:
        with Image.open(io.BytesIO(page.png_bytes)) as image:
            grayscale = image.convert("L").resize((128, 128))
            mean = float(ImageStat.Stat(grayscale).mean[0])
            dark = sum(grayscale.histogram()[:245])
            return mean < 254.5 or dark / (128 * 128) >= 0.001
    except (OSError, ValueError) as error:
        raise ValueError("rendered page image is invalid") from error


def _split_usage(usage: TokenUsage, count: int) -> tuple[TokenUsage, ...]:
    if count < 1:
        raise ValueError("usage split count must be positive")

    def split(value: int) -> tuple[int, ...]:
        quotient, remainder = divmod(value, count)
        return tuple(quotient + (index < remainder) for index in range(count))

    uncached = usage.input_tokens - usage.cached_input_tokens - usage.cache_write_tokens
    uncached_parts = split(uncached)
    cached_parts = split(usage.cached_input_tokens)
    write_parts = split(usage.cache_write_tokens)
    output_parts = split(usage.output_tokens)
    reasoning_parts = split(usage.reasoning_tokens)
    return tuple(
        TokenUsage(
            input_tokens=uncached_parts[index] + cached_parts[index] + write_parts[index],
            cached_input_tokens=cached_parts[index],
            cache_write_tokens=write_parts[index],
            output_tokens=output_parts[index],
            reasoning_tokens=reasoning_parts[index],
        )
        for index in range(count)
    )


def validate_page_extraction(extraction: PageExtraction) -> None:
    """Reject semantically invalid ranges before document rendering."""

    markdown_end = len(extraction.markdown)
    if extraction.markdown.strip() and not extraction.children:
        raise ValueError("nonempty page markdown requires grounded elements")
    previous_start = -1
    for element in extraction.children:
        parent = element.grounding.range
        if parent.end > markdown_end or parent.start < previous_start:
            raise ValueError("page element ranges must be bounded and in reading order")
        previous_start = parent.start
        if isinstance(element, DraftTable):
            if not element.children:
                raise ValueError("table elements require grounded table cells")
            for child in element.children:
                child_range = child.grounding.range
                if child_range.start < parent.start or child_range.end > parent.end:
                    raise ValueError("child range must be contained by its element")
                for atomic in child.atomic_grounding:
                    if atomic.range.start < child_range.start or atomic.range.end > child_range.end:
                        raise ValueError("atomic range must be contained by its parent")
        else:
            for atomic in element.atomic_grounding:
                if atomic.range.start < parent.start or atomic.range.end > parent.end:
                    raise ValueError("atomic range must be contained by its element")


def _read_usage(response: Any) -> TokenUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return TokenUsage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        cached_input_tokens=int(getattr(input_details, "cached_tokens", 0) or 0),
        cache_write_tokens=int(getattr(input_details, "cache_write_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        reasoning_tokens=int(getattr(output_details, "reasoning_tokens", 0) or 0),
    )
