from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from ade_app.provenance import (
    BatchManifest,
    DocumentManifest,
    TokenUsageManifest,
    governance_policy_fields,
    validate_batch_manifest,
    validate_document_manifest,
)

HASH = "a" * 64


def _usage() -> dict[str, int]:
    return {
        "input_tokens": 10,
        "cached_input_tokens": 2,
        "cache_write_tokens": 1,
        "output_tokens": 3,
        "reasoning_tokens": 4,
    }


def _document_manifest() -> dict[str, object]:
    usage = _usage()
    return {
        **governance_policy_fields(),
        "manifest_schema_version": 9,
        "generated_at_utc": "2026-09-01T00:00:00+00:00",
        "application": {"name": "ade-app", "version": "0.1.0"},
        "source_sha256": HASH,
        "raster_dpi": 300,
        "rendered_pages": [{"source_page": 1, "sha256": HASH, "width": 100, "height": 200}],
        "quality_profile": {
            "path": "profiles/quality.json",
            "sha256": HASH,
            "routing_mode": "quality_gated",
        },
        "source_filename": "sample.pdf",
        "selected_pages": [1],
        "job_id": "parse-01m1be6gqfkfrk72qg4jf93fqz",
        "model_provider": "OpenAI",
        "endpoint": "/v1/responses",
        "provider_response_storage": False,
        "model": "gpt-5.6-terra",
        "model_cascade": [
            {"model": "gpt-5.6-terra", "reasoning_effort": "medium"},
            {"model": "gpt-5.6-sol", "reasoning_effort": "low"},
        ],
        "peer_evidence_count": 0,
        "prompt_sha256": {"page_extraction.md": HASH},
        "completed_page_count": 1,
        "failed_page_count": 0,
        "review_required": False,
        "review_state": "not_required",
        "needs_review_segment_count": 0,
        "needs_review_field_count": 0,
        "artifact_schema_version": 3,
        "preprocessing": {
            "renderer": "PyMuPDF",
            "opencv_version": "5.0.0",
            "conditional_transforms": ["deskew", "denoise", "contrast"],
        },
        "routing": {
            "layout_engine": "PP-StructureV3",
            "form_detection": "OpenCV geometry + Terra semantics",
            "policy": "calibrated_fail_closed",
            "full_page_fallback_count": 1,
        },
        "usage": usage,
        "estimated_cost_usd": "0.001",
        "api_call_count": 1,
        "routing_call_count": 0,
        "retry_count": 0,
        "pages": [
            {
                "source_page": 1,
                "status": "ok",
                **usage,
                "estimated_cost_usd": "0.001",
                "elapsed_ms": 10,
                "range_repairs": 0,
                "attempts": 1,
                "api_call_count": 1,
                "routing_call_count": 0,
                "retry_count": 0,
                "failure_reason": None,
                "response_id": "resp-1",
                "request_id": "req-1",
                "models_used": ["gpt-5.6-terra"],
                "usage_by_model": {"gpt-5.6-terra": {**usage, "estimated_cost_usd": "0.001"}},
                "segments": [
                    {
                        "segment_id": "p1-s0",
                        "final_route": "verification",
                        "segment_index": 0,
                        "final_score": 100.0,
                        "status": "accepted_quality",
                        "reasons": [],
                        "structural_conflicts": [],
                        "unresolved_fields": [],
                        "attempts": [
                            {
                                "model": "gpt-5.6-terra",
                                "reasoning_effort": "medium",
                                "score": 100.0,
                                "accepted": True,
                                "input_tokens": 0,
                                "cached_input_tokens": 0,
                                "cache_write_tokens": 0,
                                "output_tokens": 0,
                                "reasoning_tokens": 0,
                                "estimated_cost_usd": "0",
                                "response_id": "resp-1",
                                "request_id": "req-1",
                                "failure_reason": None,
                                "stage": "primary",
                                "peer_source_page": None,
                                "disagreement_count": 0,
                                "batch_index": None,
                                "api_call_count": 1,
                                "retry_count": 0,
                            }
                        ],
                    }
                ],
            }
        ],
        "annotation_limitations": [],
        "artifact_sha256": {
            "markdown_sha256": HASH,
            "json_sha256": HASH,
            "annotated_pdf_sha256": HASH,
        },
    }


def test_document_manifest_is_strict_and_preserves_accounting() -> None:
    value = _document_manifest()

    serialized = validate_document_manifest(value)

    assert DocumentManifest.model_validate(serialized).api_call_count == 1
    assert serialized["usage"] == _usage()
    assert serialized["pages"][0]["usage_by_model"]["gpt-5.6-terra"] == {
        **_usage(),
        "estimated_cost_usd": "0.001",
    }


def test_document_manifest_rejects_unknown_and_inconsistent_fields() -> None:
    unknown = _document_manifest()
    unknown["invented"] = True
    inconsistent = deepcopy(_document_manifest())
    inconsistent["failed_page_count"] = 1

    with pytest.raises(ValidationError, match="extra_forbidden"):
        validate_document_manifest(unknown)
    with pytest.raises(ValidationError, match="failed_page_count"):
        validate_document_manifest(inconsistent)


def test_token_usage_manifest_rejects_invalid_cache_accounting() -> None:
    with pytest.raises(ValidationError, match="cannot exceed"):
        TokenUsageManifest(
            input_tokens=5,
            cached_input_tokens=4,
            cache_write_tokens=2,
            output_tokens=0,
        )


def test_batch_manifest_validates_child_totals() -> None:
    usage = _usage()
    value = {
        **governance_policy_fields(),
        "manifest_schema_version": 9,
        "file_count": 1,
        "successful_file_count": 1,
        "partial_file_count": 0,
        "failed_file_count": 0,
        "review_required": False,
        "review_state": "not_required",
        "review_required_file_count": 0,
        "selected_page_count": 1,
        "completed_page_count": 1,
        "failed_page_count": 0,
        "model_provider": "OpenAI",
        "endpoint": "/v1/responses",
        "provider_response_storage": False,
        "model_cascade": [
            {"model": "gpt-5.6-terra", "reasoning_effort": "medium"},
            {"model": "gpt-5.6-sol", "reasoning_effort": "low"},
        ],
        "usage": usage,
        "estimated_cost_usd": "0.001",
        "api_call_count": 1,
        "routing_call_count": 0,
        "retry_count": 0,
        "files": [
            {
                "item_id": "sample",
                "source_filename": "sample.pdf",
                "selected_pages": [1],
                "status": "ok",
                "review_required": False,
                "review_state": "not_required",
                "failure_reason": None,
                "usage": usage,
                "estimated_cost_usd": "0.001",
                "api_call_count": 1,
                "routing_call_count": 0,
                "retry_count": 0,
            }
        ],
    }

    serialized = validate_batch_manifest(value)

    assert BatchManifest.model_validate(serialized).file_count == 1
    assert serialized["api_call_count"] == 1


def test_legacy_manifest_version_is_explicit():
    value = _document_manifest()
    value["manifest_schema_version"] = 8
    value["artifact_schema_version"] = 2
    assert validate_document_manifest(value)["artifact_schema_version"] == 2
    value["artifact_schema_version"] = 3
    with pytest.raises(ValidationError, match="artifact_schema_version"):
        validate_document_manifest(value)
