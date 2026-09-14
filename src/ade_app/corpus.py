"""Recursive source-to-GroundTruth corpus inventory.

Responsible for: matching each source PDF to its GroundTruth JSON/Markdown pair
by exact filename stem, and validating that pairing (readable PDF, valid
GroundTruth JSON, Markdown/JSON agreement, complete/consistent page ranges)
before a document is eligible for evaluation or calibration. Must NOT guess a
mapping when a stem is ambiguous or incomplete — such stems go to
`CorpusExclusion` and are excluded from metrics rather than paired to the
wrong GroundTruth. Must NOT write to or otherwise mutate any GroundTruth file.
See `evaluation.py` and `calibration.py`, the two callers that turn
`CorpusInventory.documents` into scored samples.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ade_app.inputs import DocumentInput
from ade_app.models import GroundTruthDocument
from ade_app.raster import get_page_count


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    stem: str
    source: Path
    groundtruth_json: Path
    groundtruth_markdown: Path
    page_count: int
    pages: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CorpusExclusion:
    stem: str
    reason: str
    detail: str
    paths: tuple[Path, ...]
    page_count: int | None


@dataclass(frozen=True, slots=True)
class CorpusInventory:
    documents: tuple[CorpusDocument, ...]
    exclusions: tuple[CorpusExclusion, ...]
    discovered_pdf_count: int
    discovered_page_count: int
    eligible_page_count: int
    excluded_page_count: int


def inventory_corpus(gt_dir: Path, source_dir: Path) -> CorpusInventory:
    """Recursively enumerate exact-stem mappings and explicit exclusions."""

    _require_directory(gt_dir)
    _require_directory(source_dir)
    source_files = _find(source_dir, ".pdf")
    json_files = _find(gt_dir, ".parse.json")
    markdown_files = _find(gt_dir, ".parse.md")
    sources = _group(source_files, ".pdf")
    jsons = _group(json_files, ".parse.json")
    markdowns = _group(markdown_files, ".parse.md")
    page_counts: dict[Path, int] = {}
    page_errors: dict[Path, str] = {}
    for source in source_files:
        try:
            page_counts[source] = get_page_count(DocumentInput(source.name, source.read_bytes()))
        except Exception as error:  # Inventory must record unreadable PDFs and continue.
            page_errors[source] = f"{type(error).__name__}: {error}"

    documents: list[CorpusDocument] = []
    exclusions: list[CorpusExclusion] = []
    mapped_sources: set[Path] = set()
    for stem in sorted(set(sources) | set(jsons) | set(markdowns)):
        source_matches = sources.get(stem, ())
        json_matches = jsons.get(stem, ())
        markdown_matches = markdowns.get(stem, ())
        paths = tuple(sorted((*source_matches, *json_matches, *markdown_matches), key=str))
        counts = (len(source_matches), len(json_matches), len(markdown_matches))
        # A stem is only mappable when exactly one of each file exists; zero of any
        # kind or more than one of any kind is excluded rather than paired by guess.
        if counts != (1, 1, 1):
            reason = "ambiguous_mapping" if any(count > 1 for count in counts) else "mapping_gap"
            exclusions.append(
                CorpusExclusion(
                    stem=stem,
                    reason=reason,
                    detail=(
                        f"source PDF: found {counts[0]}; GroundTruth JSON: found {counts[1]}; "
                        f"GroundTruth Markdown: found {counts[2]}"
                    ),
                    paths=paths,
                    page_count=sum(page_counts.get(path, 0) for path in source_matches) or None,
                )
            )
            continue

        source, json_path, markdown_path = (
            source_matches[0],
            json_matches[0],
            markdown_matches[0],
        )
        if source in page_errors:
            exclusions.append(
                CorpusExclusion(
                    stem,
                    "source_unreadable",
                    page_errors[source],
                    paths,
                    None,
                )
            )
            continue
        try:
            groundtruth = GroundTruthDocument.model_validate_json(
                json_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            exclusions.append(
                CorpusExclusion(
                    stem,
                    "invalid_groundtruth_json",
                    f"{type(error).__name__}: {error}",
                    paths,
                    page_counts[source],
                )
            )
            continue
        try:
            markdown = markdown_path.read_text(encoding="utf-8")
        except OSError as error:
            exclusions.append(
                CorpusExclusion(
                    stem,
                    "groundtruth_markdown_unreadable",
                    f"{type(error).__name__}: {error}",
                    paths,
                    page_counts[source],
                )
            )
            continue
        # Contract check: the standalone .parse.md file must be byte-identical to
        # the JSON's own markdown field, or the pair is not a trustworthy GroundTruth.
        if markdown != groundtruth.markdown:
            exclusions.append(
                CorpusExclusion(
                    stem,
                    "groundtruth_markdown_mismatch",
                    "GroundTruth Markdown does not equal the JSON markdown field",
                    paths,
                    page_counts[source],
                )
            )
            continue
        expected_pages = tuple(range(1, groundtruth.metadata.page_count + 1))
        actual_reference_pages = tuple(
            page.grounding.page for page in groundtruth.structure.children
        )
        if actual_reference_pages != expected_pages:
            exclusions.append(
                CorpusExclusion(
                    stem,
                    "incomplete_groundtruth_pages",
                    f"expected pages {expected_pages}; found {actual_reference_pages}",
                    paths,
                    page_counts[source],
                )
            )
            continue
        if page_counts[source] != groundtruth.metadata.page_count:
            exclusions.append(
                CorpusExclusion(
                    stem,
                    "page_count_mismatch",
                    (
                        f"source PDF has {page_counts[source]} pages; GroundTruth declares "
                        f"{groundtruth.metadata.page_count}"
                    ),
                    paths,
                    page_counts[source],
                )
            )
            continue
        documents.append(
            CorpusDocument(
                stem,
                source,
                json_path,
                markdown_path,
                page_counts[source],
                expected_pages,
            )
        )
        mapped_sources.add(source)

    discovered_pages = sum(page_counts.values())
    eligible_pages = sum(document.page_count for document in documents)
    return CorpusInventory(
        documents=tuple(documents),
        exclusions=tuple(exclusions),
        discovered_pdf_count=len(source_files),
        discovered_page_count=discovered_pages,
        eligible_page_count=eligible_pages,
        excluded_page_count=sum(
            count for path, count in page_counts.items() if path not in mapped_sources
        ),
    )


def _require_directory(path: Path) -> None:
    if not path.is_dir():
        raise ValueError(f"Corpus root is unavailable or not a directory: {path}")


def _find(root: Path, ending: str) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                path.resolve()
                for path in root.rglob("*")
                if path.is_file() and path.name.lower().endswith(ending)
            ),
            key=str,
        )
    )


def _group(paths: tuple[Path, ...], ending: str) -> dict[str, tuple[Path, ...]]:
    grouped: defaultdict[str, list[Path]] = defaultdict(list)
    for path in paths:
        grouped[path.name[: -len(ending)]].append(path)
    return {stem: tuple(values) for stem, values in grouped.items()}
