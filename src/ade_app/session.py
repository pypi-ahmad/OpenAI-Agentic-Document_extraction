"""Small, testable Streamlit session-state reset contract."""

from __future__ import annotations

from typing import Any

SESSION_KEYS = (
    "document_upload",
    "start_page",
    "end_page",
    "extraction_run",
    "batch_run",
    "run_progress",
    "upload_fingerprint",
    "result_item_id",
    "authorization_acknowledged",
    "evaluation_report_upload",
)
SESSION_PREFIXES = ("start_page_", "end_page_")


def reset_session_state(state: Any) -> None:
    """Clear only ADE-owned keys; never access the filesystem."""

    for key in tuple(state):
        if key in SESSION_KEYS or any(
            str(key).startswith(prefix) for prefix in SESSION_PREFIXES
        ):
            state.pop(key, None)


def clear_page_range_state(state: Any) -> None:
    """Clear dynamically keyed range widgets when the upload collection changes."""

    for key in tuple(state):
        if any(str(key).startswith(prefix) for prefix in SESSION_PREFIXES):
            state.pop(key, None)
