"""Reproducible GroundTruth evaluation using the production extraction pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from ade_app.constants import DEFAULT_DPI, MODEL_CASCADE, MODEL_ID
from ade_app.corpus import CorpusDocument, CorpusExclusion, inventory_corpus
from ade_app.cost import MODEL_RATES
from ade_app.evaluation_metrics import (
    aggregate_documents,
    aggregate_pages,
    character_error_rate,
    evaluate_page,
    lcs_length,
    normalize_markdown,
)
from ade_app.inputs import DocumentInput
from ade_app.models import GroundTruthDocument
from ade_app.openai_client import (
    FULL_PAGE_MAX_ATTEMPTS,
    OpenAIPageExtractor,
    active_prompt_hashes,
    build_responses_parser,
    resolve_api_key,
)
from ade_app.pipeline import ExtractionRun, extract_document

MAX_EVALUATION_REPORT_BYTES = 10 * 1024 * 1024

__all__ = [
    "CURATED_SUITE",
    "MAX_EVALUATION_REPORT_BYTES",
    "character_error_rate",
    "evaluate_document",
    "normalize_markdown",
    "parse_evaluation_report",
    "run_evaluation",
]

CURATED_SUITE: dict[str, tuple[int, ...]] = {
    "Masked Amerigroup_1": (1, 2),
    "Masked BadgeCare Plus_1": (1, 2),
    "Masked_Amerigroup_RealSolutions_1": (1, 2, 3, 4, 5),
    "Masked_Amerigroup_RealSolutions_2": (1, 2, 3),
    "PublicWaterMassMailing": (4, 5),
}


@dataclass(frozen=True, slots=True)
class Sample:
    stem: str
    source: Path
    groundtruth_json: Path
    groundtruth_markdown: Path
    pages: tuple[int, ...]


def parse_evaluation_report(data: bytes) -> dict[str, Any]:
    """Validate a bounded evaluation report without reading files or running extraction."""

    if len(data) > MAX_EVALUATION_REPORT_BYTES:
        raise ValueError("evaluation report may not exceed 10 MB")
    try:
        report = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("evaluation report must be valid UTF-8 JSON") from error
    if not isinstance(report, dict):
        raise ValueError("evaluation report must be a JSON object")
    if type(report.get("schema_version")) is not int or report["schema_version"] not in {2, 3}:
        raise ValueError("evaluation report schema_version must be 2 or 3")
    if not isinstance(report.get("overall"), dict):
        raise ValueError("evaluation report overall must be an object")
    if not isinstance(report.get("coverage"), dict):
        raise ValueError("evaluation report coverage must be an object")
    documents = report.get("documents")
    if not isinstance(documents, list) or any(not isinstance(item, dict) for item in documents):
        raise ValueError("evaluation report documents must be an array of objects")
    return report


def _select_samples(documents: tuple[CorpusDocument, ...], suite: str) -> list[Sample]:
    samples: list[Sample] = []
    for document in documents:
        pages = CURATED_SUITE.get(document.stem, ()) if suite == "curated" else document.pages
        if suite == "curated" and not pages:
            continue
        if any(page not in document.pages for page in pages):
            raise ValueError(f"suite {suite} contains an unavailable page for {document.stem}")
        samples.append(
            Sample(
                document.stem,
                document.source,
                document.groundtruth_json,
                document.groundtruth_markdown,
                pages,
            )
        )
    return samples


def evaluate_document(
    groundtruth: GroundTruthDocument, generated: GroundTruthDocument, pages: tuple[int, ...]
) -> dict[str, Any]:
    gt_pages = {page.grounding.page: page for page in groundtruth.structure.children}
    generated_pages = {page.grounding.page: page for page in generated.structure.children}
    page_metrics = [
        evaluate_page(groundtruth, generated, gt_pages[page], generated_pages.get(page))
        for page in pages
    ]
    return {
        "selected_pages": list(pages),
        "valid_json": True,
        "page_order_exact": [p.grounding.page for p in generated.structure.children] == list(pages),
        "page_order_lcs_accuracy": lcs_length(
            list(pages), [p.grounding.page for p in generated.structure.children]
        )
        / len(pages),
        "metrics": aggregate_pages(page_metrics),
        "pages": page_metrics,
        "metadata_fields": {
            "page_count_exact": generated.metadata.page_count == groundtruth.metadata.page_count,
            "range_units_exact": generated.metadata.range_units == groundtruth.metadata.range_units,
            "structure_type_exact": generated.structure.type == groundtruth.structure.type,
            "model_scored": False,
            "model_note": (
                "Expected systems use different model identifiers; not an accuracy field."
            ),
        },
    }


def run_evaluation(*, gt_dir: Path, source_dir: Path, output_root: Path, suite: str) -> Path:
    inventory = inventory_corpus(gt_dir, source_dir)
    samples = _select_samples(inventory.documents, suite)
    run_started = datetime.now(UTC)
    config = {
        "suite": suite,
        "model": MODEL_ID,
        "model_cascade": [
            {"model": model, "reasoning_effort": effort} for model, effort in MODEL_CASCADE
        ],
        "endpoint": "/v1/responses",
        "dpi": DEFAULT_DPI,
        "max_workers": 3,
        "max_full_page_attempts": FULL_PAGE_MAX_ATTEMPTS,
        "prompt_sha256": active_prompt_hashes(),
        "pages": {sample.stem: list(sample.pages) for sample in samples},
    }
    config_hash = hashlib.sha256(_json(config).encode()).hexdigest()[:10]
    run_dir = output_root / f"{run_started.strftime('%Y%m%dT%H%M%SZ')}-{config_hash}"
    run_dir.mkdir(parents=True, exist_ok=False)
    extractor = OpenAIPageExtractor(build_responses_parser(resolve_api_key())) if samples else None
    documents: list[dict[str, Any]] = []
    for sample in samples:
        document_started = time.perf_counter()
        print(f"Evaluating {sample.stem}: {len(sample.pages)} pages", flush=True)
        groundtruth = GroundTruthDocument.model_validate_json(
            sample.groundtruth_json.read_text(encoding="utf-8")
        )
        source = DocumentInput(sample.source.name, sample.source.read_bytes())
        try:
            if extractor is None:
                raise RuntimeError("extractor is unavailable for a mapped sample")
            run = extract_document(
                source,
                sample.pages,
                extractor,
                max_workers=3,
                progress=lambda completed, failed, total, page, status: print(
                    f"  {completed}/{total} pages; page={page}; status={status}; failed={failed}",
                    flush=True,
                ),
            )
            document_metrics = evaluate_document(groundtruth, run.artifact, sample.pages)
            document_record = _document_record(sample, run, document_metrics, document_started)
            _write_run_artifacts(run_dir / sample.stem, run, document_record)
        except Exception as error:  # Keep other samples measurable after document-level failure.
            failed_pages = [
                evaluate_page(
                    groundtruth,
                    groundtruth,
                    next(
                        page
                        for page in groundtruth.structure.children
                        if page.grounding.page == page_number
                    ),
                    None,
                )
                for page_number in sample.pages
            ]
            runtime_pages = {
                page.source_page: page for page in getattr(error, "pages", ())
            }
            for page in failed_pages:
                runtime = runtime_pages.get(page["page"])
                page.update(_runtime_fields(runtime))
                page["failure_reason"] = (
                    runtime.failure_reason if runtime is not None else None
                ) or str(error)
            failed_usage = getattr(error, "usage", None)
            document_record = {
                "document": sample.stem,
                "selected_pages": list(sample.pages),
                "valid_json": False,
                "elapsed_ms": round((time.perf_counter() - document_started) * 1_000),
                "failure_reason": f"{type(error).__name__}: {error}",
                "metrics": aggregate_pages(failed_pages),
                "usage": {
                    "input_tokens": getattr(failed_usage, "input_tokens", 0),
                    "cached_input_tokens": getattr(failed_usage, "cached_input_tokens", 0),
                    "cache_write_tokens": getattr(failed_usage, "cache_write_tokens", 0),
                    "output_tokens": getattr(failed_usage, "output_tokens", 0),
                    "reasoning_tokens": getattr(failed_usage, "reasoning_tokens", 0),
                },
                "estimated_cost_usd": str(getattr(error, "cost_usd", 0)),
                "api_call_count": sum(page["api_call_count"] for page in failed_pages),
                "routing_call_count": sum(
                    page["routing_call_count"] for page in failed_pages
                ),
                "retry_count": sum(page["retry_count"] for page in failed_pages),
                "pages": failed_pages,
            }
        documents.append(document_record)

    report = {
        "schema_version": 3,
        "coverage": {
            "source_pdfs_discovered": inventory.discovered_pdf_count,
            "source_pages_discovered": inventory.discovered_page_count,
            "documents_mapped": len(inventory.documents),
            "documents_excluded": len(inventory.exclusions),
            "pages_excluded": inventory.excluded_page_count,
            "documents_evaluated": len(samples),
            "selected_pages": sum(len(sample.pages) for sample in samples),
            "corpus_pages": inventory.discovered_page_count,
        },
        "mappings": [_mapping_record(document) for document in inventory.documents],
        "exclusions": [_exclusion_record(exclusion) for exclusion in inventory.exclusions],
        "configuration": config,
        "reproducibility": {
            "started_at": run_started.isoformat(),
            "python": platform.python_version(),
            "packages": {
                package: version(package)
                for package in ("openai", "pydantic", "pymupdf", "streamlit")
            },
            "prompt_sha256": active_prompt_hashes(),
            "fixed_rates_usd_per_million": {
                model: {
                    "input": str(rates[0]),
                    "cached_input": str(rates[1]),
                    "cache_write": str(rates[2]),
                    "output": str(rates[3]),
                }
                for model, rates in MODEL_RATES.items()
            },
            "sources": {
                sample.stem: {
                    "source_sha256": _sha256(sample.source),
                    "groundtruth_json_sha256": _sha256(sample.groundtruth_json),
                    "groundtruth_markdown_sha256": _sha256(sample.groundtruth_markdown),
                }
                for sample in samples
            },
        },
        "overall": aggregate_documents(documents),
        "documents": documents,
    }
    (run_dir / "report.json").write_text(_json(report), encoding="utf-8")
    (run_dir / "report.md").write_text(_report_markdown(report), encoding="utf-8")
    with zipfile.ZipFile(run_dir / "report.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(run_dir / "report.json", "report.json")
        archive.write(run_dir / "report.md", "report.md")
    return run_dir


def _document_record(
    sample: Sample,
    run: ExtractionRun,
    metrics: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    page_runtime = {page.source_page: page for page in run.pages}
    for page in metrics["pages"]:
        runtime = page_runtime[page["page"]]
        page.update(
            {
                **_runtime_fields(runtime),
                "range_repairs": runtime.range_repairs,
                "usage_note": (
                    "Usage includes only requests for which the API returned usage metadata."
                    if runtime.retry_count > 0 or runtime.status == "failed"
                    else None
                ),
                "failure_reason": runtime.failure_reason or page.get("failure_reason"),
            }
        )
    return {
        "document": sample.stem,
        **metrics,
        "elapsed_ms": round((time.perf_counter() - started) * 1_000),
        "usage": {
            "input_tokens": run.usage.input_tokens,
            "cached_input_tokens": run.usage.cached_input_tokens,
            "cache_write_tokens": run.usage.cache_write_tokens,
            "output_tokens": run.usage.output_tokens,
            "reasoning_tokens": run.usage.reasoning_tokens,
        },
        "estimated_cost_usd": str(run.cost_usd),
        "api_call_count": sum(page["api_call_count"] for page in metrics["pages"]),
        "routing_call_count": sum(page["routing_call_count"] for page in metrics["pages"]),
        "retry_count": sum(page["retry_count"] for page in metrics["pages"]),
        "failure_reason": None,
    }


def _runtime_fields(runtime: Any | None) -> dict[str, Any]:
    if runtime is None:
        return {
            "elapsed_ms": 0,
            "usage": {
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
            },
            "estimated_cost_usd": "0",
            "attempts": 0,
            "api_call_count": 0,
            "routing_call_count": 0,
            "retry_count": 0,
        }
    return {
        "elapsed_ms": runtime.elapsed_ms,
        "usage": {
            "input_tokens": runtime.usage.input_tokens,
            "cached_input_tokens": runtime.usage.cached_input_tokens,
            "cache_write_tokens": runtime.usage.cache_write_tokens,
            "output_tokens": runtime.usage.output_tokens,
            "reasoning_tokens": runtime.usage.reasoning_tokens,
        },
        "estimated_cost_usd": str(runtime.cost_usd),
        "attempts": runtime.attempts,
        "api_call_count": runtime.api_call_count,
        "routing_call_count": runtime.routing_call_count,
        "retry_count": runtime.retry_count,
    }


def _write_run_artifacts(
    directory: Path, run: ExtractionRun, document_record: dict[str, Any]
) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    (directory / run.markdown_filename).write_text(run.artifact.markdown, encoding="utf-8")
    (directory / run.json_filename).write_text(run.json_text, encoding="utf-8")
    (directory / run.annotated_pdf_filename).write_bytes(run.annotated_pdf)
    (directory / run.zip_filename).write_bytes(run.zip_bytes)
    (directory / "metrics.json").write_text(_json(document_record), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping_record(document: CorpusDocument) -> dict[str, Any]:
    return {
        "stem": document.stem,
        "source": str(document.source),
        "groundtruth_json": str(document.groundtruth_json),
        "groundtruth_markdown": str(document.groundtruth_markdown),
        "page_count": document.page_count,
        "pages": list(document.pages),
    }


def _exclusion_record(exclusion: CorpusExclusion) -> dict[str, Any]:
    return {
        "stem": exclusion.stem,
        "reason": exclusion.reason,
        "detail": exclusion.detail,
        "paths": [str(path) for path in exclusion.paths],
        "page_count": exclusion.page_count,
    }


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def _report_markdown(report: dict[str, Any]) -> str:
    overall = report["overall"]
    coverage = report["coverage"]
    lines = [
        "# ADE GroundTruth evaluation",
        "",
        f"Coverage: {coverage['selected_pages']}/{coverage['corpus_pages']} pages across "
        f"{coverage['documents_evaluated']} documents.",
        "",
        f"- Mapped documents: {coverage['documents_mapped']}",
        f"- Excluded documents: {coverage['documents_excluded']}",
        f"- Excluded source pages: {coverage['pages_excluded']}",
        f"- Valid JSON rate: {overall['valid_json_rate']:.3f}",
        f"- Page success rate: {overall['page_success_rate']:.3f}",
        f"- Element F1: {overall['element_prf_micro']['f1']:.3f}",
        f"- Exact field F1: {overall['field_exact_prf_micro']['f1']:.3f}",
        f"- Normalized field F1: {overall['field_normalized_prf_micro']['f1']:.3f}",
        f"- Markdown CER: {overall['markdown_cer_micro']:.3f}",
        f"- Exact table-shape F1: {overall['table_prf_micro']['f1']:.3f}",
        f"- Table-cell value F1: {overall['table_cell_value_prf_micro']['f1']:.3f}",
        f"- Estimated cost: ${overall['estimated_cost_usd']}",
        f"- Fallback pages: {overall['fallback_page_count']}",
        f"- Retries: {overall['retry_count']}",
        "",
        "| Document | Page success | Element F1 | Markdown similarity | Cost |",
        "|---|---:|---:|---:|---:|",
        *[
            (
                f"| {document['document']} | {document['metrics']['page_success_rate']:.3f} "
                f"| {document['metrics']['element_prf_micro']['f1']:.3f} "
                f"| {document['metrics']['markdown_similarity_micro']:.3f} "
                f"| ${document['estimated_cost_usd']} |"
            )
            for document in report["documents"]
        ],
        "",
        *(
            [
                "## Exclusions",
                "",
                *[
                    f"- `{item['stem']}` — {item['reason']}: {item['detail']}"
                    for item in report["exclusions"]
                ],
                "",
            ]
            if report["exclusions"]
            else []
        ),
        "Usage and cost include billed failed attempts whenever the API exposed usage metadata.",
        "",
        (
            "Measured only on selected pages. This report does not claim "
            "LandingAI-equivalent accuracy."
        ),
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate ADE output against local GroundTruth pairs"
    )
    parser.add_argument("--ground-truth-dir", type=Path, default=Path("data/GroundTruths"))
    parser.add_argument("--source-dir", type=Path, default=Path("data/Original Pdfs"))
    parser.add_argument("--output-root", type=Path, default=Path("evaluation/runs"))
    parser.add_argument("--suite", choices=("curated", "full"), default="curated")
    parser.add_argument(
        "--acknowledge-sensitive-output",
        action="store_true",
        help="Confirm that generated evaluation artifacts may contain sensitive document data.",
    )
    args = parser.parse_args()
    if not args.acknowledge_sensitive_output:
        parser.error(
            "--acknowledge-sensitive-output is required because evaluation artifacts "
            "may contain sensitive document data"
        )
    try:
        result = run_evaluation(
            gt_dir=args.ground_truth_dir,
            source_dir=args.source_dir,
            output_root=args.output_root,
            suite=args.suite,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
    print(result)


if __name__ == "__main__":
    main()
