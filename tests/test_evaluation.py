from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest

from ade_app.corpus import inventory_corpus
from ade_app.evaluation import (
    CURATED_SUITE,
    MAX_EVALUATION_REPORT_BYTES,
    character_error_rate,
    evaluate_document,
    main,
    normalize_markdown,
    parse_evaluation_report,
    run_evaluation,
)
from ade_app.evaluation_metrics import aggregate_documents
from ade_app.models import PageExtraction
from ade_app.rendering import PageOutcome, render_document

JOB_ID = "parse-01m1be6gqfkfrk72qg4jf93fqz"


def test_markdown_normalization_preserves_structure() -> None:
    value = "#  Heading  \r\n\r\n<table><tr><td>A  B</td></tr></table>\r\n<!-- doc_id=x -->"

    assert normalize_markdown(value) == ("# Heading\n\n<table><tr><td>A B</td></tr></table>")


def test_character_error_rate_is_exact() -> None:
    edits, rate, similarity = character_error_rate("abc", "adc")

    assert edits == 1
    assert rate == 1 / 3
    assert similarity == pytest.approx(2 / 3)


def test_character_error_rate_handles_long_shared_content_exactly() -> None:
    shared = "stable GroundTruth content\n" * 20_000

    assert character_error_rate(shared, shared) == (0, 0.0, 1.0)
    edits, rate, similarity = character_error_rate(
        f"{shared}expected{shared}", f"{shared}candidate{shared}"
    )

    assert edits == 8
    assert rate == pytest.approx(8 / len(f"{shared}expected{shared}"))
    assert similarity == pytest.approx(1 - rate)


def test_parse_evaluation_report_accepts_supported_object() -> None:
    report = {
        "schema_version": 2,
        "overall": {"valid_json_rate": 1.0},
        "coverage": {"documents_evaluated": 1},
        "documents": [{"document": "sample"}],
    }

    assert parse_evaluation_report(json.dumps(report).encode()) == report


@pytest.mark.parametrize(
    "report",
    [
        [],
        {"schema_version": 1, "overall": {}, "coverage": {}, "documents": []},
        {"schema_version": 2, "overall": [], "coverage": {}, "documents": []},
        {"schema_version": 2, "overall": {}, "coverage": [], "documents": []},
        {"schema_version": 2, "overall": {}, "coverage": {}, "documents": [{}, "bad"]},
    ],
)
def test_parse_evaluation_report_rejects_invalid_shape(report: object) -> None:
    with pytest.raises(ValueError, match="evaluation report"):
        parse_evaluation_report(json.dumps(report).encode())


def test_parse_evaluation_report_rejects_oversized_input() -> None:
    with pytest.raises(ValueError, match="10 MB"):
        parse_evaluation_report(b" " * (MAX_EVALUATION_REPORT_BYTES + 1))


def test_evaluation_aggregation_uses_explicit_call_accounting() -> None:
    report = aggregate_documents(
        [
            {
                "valid_json": True,
                "usage": {"input_tokens": 1},
                "pages": [
                    {
                        "status": "ok",
                        "attempts": 99,
                        "api_call_count": 3,
                        "routing_call_count": 2,
                        "retry_count": 1,
                        "usage": {
                            "input_tokens": 1,
                            "cached_input_tokens": 0,
                            "cache_write_tokens": 0,
                            "output_tokens": 0,
                            "reasoning_tokens": 0,
                        },
                    }
                ],
            }
        ]
    )

    assert report["api_call_count"] == 3
    assert report["routing_call_count"] == 2
    assert report["fallback_page_count"] == 1
    assert report["retry_count"] == 1
    assert report["usage_complete"] is True


def test_evaluation_prefers_ledger_counts_and_actual_full_page_fallbacks() -> None:
    report = aggregate_documents(
        [
            {
                "api_call_count": 7,
                "pages": [
                    {
                        "status": "ok",
                        "api_call_count": 3,
                        "routing_call_count": 2,
                        "full_page_fallback": False,
                    }
                ],
            }
        ]
    )
    assert report["api_call_count"] == 7
    assert report["fallback_page_count"] == 0
    assert report["fallback_unknown_page_count"] == 0


def test_identical_document_scores_perfectly(sample_page: PageExtraction) -> None:
    artifact = render_document(
        page_count=1,
        outcomes=[PageOutcome(source_page=1, extraction=sample_page)],
        duration_ms=1,
        cost_usd=Decimal(0),
        job_id=JOB_ID,
    )

    result = evaluate_document(artifact, artifact, (1,))

    assert result["metrics"]["element_prf_micro"]["f1"] == 1
    assert result["metrics"]["field_exact_prf_micro"]["f1"] == 1
    assert result["metrics"]["field_normalized_prf_micro"]["f1"] == 1
    assert result["metrics"]["table_cell_value_prf_micro"]["f1"] == 1
    assert result["metrics"]["markdown_cer_micro"] == 0
    assert result["page_order_exact"] is True


def test_curated_suite_maps_all_local_sources() -> None:
    inventory = inventory_corpus(Path("data/GroundTruths"), Path("data/Original Pdfs"))

    assert len(inventory.documents) == 5
    assert sum(len(CURATED_SUITE[document.stem]) for document in inventory.documents) == 14


def test_inventory_maps_recursive_pair_and_enumerates_pages(
    tmp_path: Path, sample_page: PageExtraction
) -> None:
    source_dir, gt_dir = tmp_path / "sources", tmp_path / "groundtruths"
    _write_pdf(source_dir / "nested" / "sample.pdf", page_count=2)
    _write_groundtruth(gt_dir / "nested", "sample", sample_page, page_count=2)

    inventory = inventory_corpus(gt_dir, source_dir)

    assert inventory.discovered_pdf_count == 1
    assert inventory.discovered_page_count == 2
    assert inventory.excluded_page_count == 0
    assert inventory.exclusions == ()
    assert inventory.documents[0].pages == (1, 2)


def test_inventory_excludes_gap_without_discarding_valid_mapping(
    tmp_path: Path, sample_page: PageExtraction
) -> None:
    source_dir, gt_dir = tmp_path / "sources", tmp_path / "groundtruths"
    _write_pdf(source_dir / "valid.pdf")
    _write_groundtruth(gt_dir, "valid", sample_page)
    _write_pdf(source_dir / "missing-reference.pdf")

    inventory = inventory_corpus(gt_dir, source_dir)

    assert [document.stem for document in inventory.documents] == ["valid"]
    assert len(inventory.exclusions) == 1
    assert inventory.exclusions[0].stem == "missing-reference"
    assert inventory.exclusions[0].reason == "mapping_gap"
    assert "GroundTruth JSON: found 0" in inventory.exclusions[0].detail


def test_inventory_excludes_page_count_mismatch(
    tmp_path: Path, sample_page: PageExtraction
) -> None:
    source_dir, gt_dir = tmp_path / "sources", tmp_path / "groundtruths"
    _write_pdf(source_dir / "sample.pdf", page_count=2)
    _write_groundtruth(gt_dir, "sample", sample_page)

    inventory = inventory_corpus(gt_dir, source_dir)

    assert inventory.documents == ()
    assert inventory.exclusions[0].reason == "page_count_mismatch"
    assert inventory.exclusions[0].page_count == 2


def test_evaluation_report_records_excluded_unmapped_pdf(tmp_path: Path) -> None:
    source_dir, gt_dir = tmp_path / "sources", tmp_path / "groundtruths"
    _write_pdf(source_dir / "unmapped.pdf")
    gt_dir.mkdir()

    run_dir = run_evaluation(
        gt_dir=gt_dir,
        source_dir=source_dir,
        output_root=tmp_path / "runs",
        suite="full",
    )
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))

    assert report["schema_version"] == 4
    assert report["coverage"]["documents_evaluated"] == 0
    assert report["coverage"]["documents_excluded"] == 1
    assert report["coverage"]["pages_excluded"] == 1
    assert report["exclusions"][0]["stem"] == "unmapped"
    assert report["overall"]["valid_json_rate"] == 0.0
    assert report["overall"]["retry_count"] == 0
    assert report["configuration"]["max_full_page_attempts"] == 2


def test_evaluation_cli_requires_sensitive_output_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["ade-evaluate"])

    with pytest.raises(SystemExit) as captured:
        main()

    assert captured.value.code == 2


def test_inventory_missing_root_reports_exact_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(ValueError, match=str(missing).replace("\\", "\\\\")):
        inventory_corpus(missing, tmp_path)


def _write_pdf(path: Path, page_count: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    for _ in range(page_count):
        document.new_page()
    path.write_bytes(document.tobytes())
    document.close()


def _write_groundtruth(
    directory: Path,
    stem: str,
    page: PageExtraction,
    page_count: int = 1,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    artifact = render_document(
        page_count=page_count,
        outcomes=[
            PageOutcome(source_page=source_page, extraction=page)
            for source_page in range(1, page_count + 1)
        ],
        duration_ms=1,
        cost_usd=Decimal(0),
        job_id=JOB_ID,
    )
    (directory / f"{stem}.parse.json").write_text(
        artifact.model_dump_json(indent=2), encoding="utf-8"
    )
    (directory / f"{stem}.parse.md").write_text(artifact.markdown, encoding="utf-8")
