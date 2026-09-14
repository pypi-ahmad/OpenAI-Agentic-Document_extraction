from __future__ import annotations

import pytest
from pydantic import ValidationError

from ade_app.models import (
    MAX_PAGE_ELEMENTS,
    AuditedSemanticPageExtraction,
    SegmentAudit,
)
from ade_app.security import is_loopback_address, public_extraction_error


def test_only_loopback_server_addresses_are_allowed() -> None:
    assert is_loopback_address("127.0.0.1")
    assert is_loopback_address("::1")
    assert is_loopback_address("localhost")
    assert not is_loopback_address(None)
    assert not is_loopback_address("0.0.0.0")
    assert not is_loopback_address("192.168.1.20")


def test_internal_errors_are_not_exposed() -> None:
    assert public_extraction_error(RuntimeError("provider failed at C:\\secret\\file")) == (
        "Extraction could not be completed. Check the document and app configuration."
    )
    assert public_extraction_error(
        RuntimeError("Required credential OPENAI_API_KEY is unavailable")
    ) == ("Required credential OPENAI_API_KEY is unavailable")


def test_model_page_element_count_is_bounded(audited_semantic_page) -> None:
    payload = audited_semantic_page.model_dump(mode="json")
    payload["children"] = payload["children"] * (MAX_PAGE_ELEMENTS + 1)
    payload["audits"] = [
        SegmentAudit(
            segment_index=index,
            completeness="complete",
            image_agreement="supported",
            findings=[],
        ).model_dump(mode="json")
        for index in range(MAX_PAGE_ELEMENTS + 1)
    ]

    with pytest.raises(ValidationError, match="at most 64 items"):
        AuditedSemanticPageExtraction.model_validate(payload)
