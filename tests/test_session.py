from ade_app.session import reset_session_state


def test_reset_removes_only_ade_session_keys() -> None:
    state = {
        "document_upload": object(),
        "start_page": 1,
        "end_page": 2,
        "extraction_run": object(),
        "batch_run": object(),
        "run_progress": 50,
        "upload_fingerprint": "abc",
        "result_item_id": "item-1",
        "authorization_acknowledged": True,
        "evaluation_report_upload": object(),
        "start_page_item-1": 1,
        "end_page_item-1": 2,
        "unrelated": "preserved",
    }

    reset_session_state(state)

    assert state == {"unrelated": "preserved"}
