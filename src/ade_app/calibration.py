"""Live GroundTruth calibration for segment-quality routing."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from ade_app.constants import DEFAULT_DPI, PRIMARY_MODEL, QUALITY_PROFILE_PATH, QUALITY_THRESHOLD
from ade_app.corpus import CorpusDocument, inventory_corpus
from ade_app.cost import TokenUsage, calculate_cost
from ade_app.evaluation import CURATED_SUITE
from ade_app.evaluation_metrics import align_elements, box_iou, element_text, inline_text
from ade_app.inputs import DocumentInput
from ade_app.models import AuditedPageExtraction, GroundTruthDocument, TableElement
from ade_app.openai_client import (
    PROMPT_PATH,
    OpenAIPageExtractor,
    build_responses_parser,
    resolve_api_key,
)
from ade_app.quality import (
    CalibrationSample,
    fit_quality_profile,
    profile_json,
    segment_features,
)
from ade_app.raster import RenderedPage, rasterize_document
from ade_app.rendering import PageOutcome, generate_job_id, render_document


@dataclass(frozen=True, slots=True)
class CalibrationPage:
    document: str
    source_page: int
    extraction: AuditedPageExtraction
    usage: TokenUsage


def calibrate(
    *,
    gt_dir: Path,
    source_dir: Path,
    profile_path: Path,
    report_path: Path,
    max_workers: int = 3,
    suite: str = "full",
) -> Path:
    inventory = inventory_corpus(gt_dir, source_dir)
    if not inventory.documents:
        raise ValueError("no complete source-to-GroundTruth mappings are available")
    extractor = OpenAIPageExtractor(build_responses_parser(resolve_api_key()), profile_path=None)
    rendered: list[tuple[CorpusDocument, RenderedPage]] = []
    groundtruths: dict[str, GroundTruthDocument] = {}
    for document in inventory.documents:
        selected_pages = _select_pages(document, suite)
        if not selected_pages:
            continue
        groundtruths[document.stem] = GroundTruthDocument.model_validate_json(
            document.groundtruth_json.read_text(encoding="utf-8")
        )
        source = DocumentInput(document.source.name, document.source.read_bytes())
        rendered.extend(
            (document, page) for page in rasterize_document(source, selected_pages, DEFAULT_DPI)
        )

    pages: list[CalibrationPage] = []
    failures: list[dict[str, object]] = []
    completed = 0
    with ThreadPoolExecutor(max_workers=min(max_workers, len(rendered))) as pool:
        futures = {
            pool.submit(
                extractor.extract_for_calibration,
                page,
                job_id=generate_job_id(),
                page_count=document.page_count,
            ): (document, page)
            for document, page in rendered
        }
        for future in as_completed(futures):
            document, page = futures[future]
            try:
                extraction, usage = future.result()
                pages.append(CalibrationPage(document.stem, page.source_page, extraction, usage))
            except Exception as error:  # Keep other pages useful for calibration.
                failures.append(
                    {
                        "document": document.stem,
                        "page": page.source_page,
                        "reason": f"{type(error).__name__}: {error}",
                    }
                )
            completed += 1
            print(
                f"Calibration {completed}/{len(rendered)} pages; failures={len(failures)}",
                flush=True,
            )

    samples: list[CalibrationSample] = []
    unmatched_reference = 0
    for page in pages:
        groundtruth = groundtruths[page.document]
        reference_page = next(
            item
            for item in groundtruth.structure.children
            if item.grounding.page == page.source_page
        )
        candidate = render_document(
            page_count=groundtruth.metadata.page_count,
            outcomes=[PageOutcome(page.source_page, extraction=page.extraction)],
            duration_ms=0,
            cost_usd=calculate_cost(page.usage, PRIMARY_MODEL[0]),
            model_version=PRIMARY_MODEL[0],
        )
        candidate_page = candidate.structure.children[0]
        matches = align_elements(groundtruth, candidate, reference_page, candidate_page)
        reference_by_candidate = {right: left for left, right in matches}
        unmatched_reference += len(reference_page.children) - len(matches)
        extraction = page.extraction
        for index, audit in enumerate(extraction.audits):
            features, _ = segment_features(extraction, index, audit)
            reference_index = reference_by_candidate.get(index)
            actual = 0.0
            if reference_index is not None:
                expected = reference_page.children[reference_index]
                generated = candidate_page.children[index]
                expected_text = inline_text(element_text(groundtruth, expected))
                generated_text = inline_text(element_text(candidate, generated))
                text_score = SequenceMatcher(None, expected_text, generated_text).ratio()
                structure_score = _structure_score(expected, generated)
                box_score = box_iou(expected.grounding.box, generated.grounding.box)
                actual = 100 * (0.90 * text_score + 0.05 * structure_score + 0.05 * box_score)
            samples.append(CalibrationSample(page.document, features, actual))

    prompt_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (PROMPT_PATH,)
    }
    gt_hashes = {
        document.groundtruth_json.name: hashlib.sha256(
            document.groundtruth_json.read_bytes()
        ).hexdigest()
        for document in inventory.documents
    }
    profile = fit_quality_profile(
        samples,
        prompt_hashes=prompt_hashes,
        groundtruth_hashes=gt_hashes,
        threshold=QUALITY_THRESHOLD,
        model_id=PRIMARY_MODEL[0],
        reasoning_effort=PRIMARY_MODEL[1],
    )
    report = {
        "suite": suite,
        "model": PRIMARY_MODEL[0],
        "reasoning_effort": PRIMARY_MODEL[1],
        "documents": len(inventory.documents),
        "pages_requested": len(rendered),
        "pages_succeeded": len(pages),
        "failures": failures,
        "segments": len(samples),
        "unmatched_reference_segments": unmatched_reference,
        "usage": {
            "input_tokens": sum(page.usage.input_tokens for page in pages),
            "cached_input_tokens": sum(page.usage.cached_input_tokens for page in pages),
            "output_tokens": sum(page.usage.output_tokens for page in pages),
        },
        "estimated_cost_usd": str(
            sum(
                (calculate_cost(page.usage, PRIMARY_MODEL[0]) for page in pages),
                start=calculate_cost(TokenUsage(), PRIMARY_MODEL[0]),
            )
        ),
        "validation": profile.validation,
        "profile_written": False,
    }
    if _profile_can_be_promoted(profile.validation, failures):
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(profile_json(profile), encoding="utf-8")
        report["profile_written"] = True
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not report["profile_written"]:
        raise RuntimeError(
            "calibration profile rejected; require successful pages, safe false accepts, "
            "and nonzero held-out acceptance"
        )
    return profile_path


def _profile_can_be_promoted(
    validation: dict[str, float | int], failures: list[dict[str, object]]
) -> bool:
    """Require useful held-out coverage as well as a safe false-accept rate."""

    return (
        not failures
        and int(validation.get("accepted_count", 0)) > 0
        and float(validation.get("false_accept_rate", 1.0)) <= 0.10
    )


def _select_pages(document: CorpusDocument, suite: str) -> tuple[int, ...]:
    if suite == "full":
        return document.pages
    if suite != "curated":
        raise ValueError(f"unsupported calibration suite: {suite}")
    pages = CURATED_SUITE.get(document.stem, ())
    if any(page not in document.pages for page in pages):
        raise ValueError(f"curated suite contains an unavailable page for {document.stem}")
    return pages


def _structure_score(expected: object, generated: object) -> float:
    if type(expected) is not type(generated):
        return 0.0
    if not isinstance(expected, TableElement) or not isinstance(generated, TableElement):
        return 1.0
    expected_cells = [
        (cell.row, cell.col, cell.rowspan, cell.colspan) for cell in expected.children
    ]
    generated_cells = [
        (cell.row, cell.col, cell.rowspan, cell.colspan) for cell in generated.children
    ]
    return SequenceMatcher(None, expected_cells, generated_cells).ratio()


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate the ADE segment-quality router")
    parser.add_argument("--ground-truth-dir", type=Path, default=Path("data/GroundTruths"))
    parser.add_argument("--source-dir", type=Path, default=Path("data/Original Pdfs"))
    parser.add_argument("--profile", type=Path, default=Path(QUALITY_PROFILE_PATH))
    parser.add_argument("--report", type=Path, default=Path("evaluation/calibration-report.json"))
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--suite", choices=("curated", "full"), default="full")
    args = parser.parse_args()
    try:
        result = calibrate(
            gt_dir=args.ground_truth_dir,
            source_dir=args.source_dir,
            profile_path=args.profile,
            report_path=args.report,
            max_workers=args.max_workers,
            suite=args.suite,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
    print(result)


if __name__ == "__main__":
    main()
