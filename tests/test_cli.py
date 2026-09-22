import pytest

from ade_app.cli import _exit_code, _write_outputs
from ade_app.models import DocumentResult, PageResult


def _document(*statuses: str) -> DocumentResult:
    return DocumentResult(
        source_filename="scan.pdf",
        pages=[
            PageResult(page=index, width=100, height=100, blocks=[], status=status)
            for index, status in enumerate(statuses, start=1)
        ],
        markdown="",
    )


def test_cli_exit_code_reports_page_outcomes() -> None:
    assert _exit_code(_document("ok", "ok")) == 0
    assert _exit_code(_document("ok", "partial")) == 2
    assert _exit_code(_document("partial", "failed")) == 1


def test_cli_preflights_all_output_conflicts(tmp_path) -> None:
    (tmp_path / "later.json").write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError, match=r"later\.json"):
        _write_outputs(
            tmp_path,
            {"first.md": b"new", "later.json": b"replacement"},
            overwrite=False,
        )

    assert not (tmp_path / "first.md").exists()
    assert (tmp_path / "later.json").read_text(encoding="utf-8") == "existing"
