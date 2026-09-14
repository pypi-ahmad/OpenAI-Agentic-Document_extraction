"""GroundTruth schema contract discovery and corpus profile verification.

Responsible for inspecting paired GroundTruth artifacts (*.parse.json and *.parse.md),
verifying structural key ordering and element counts, and emitting content-free profile summaries.
Must NOT store extracted text or document values in output profiles.
Next: ade_app.models for schema definitions, or ade_app.calibration for routing calibration.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ade_app.models import GroundTruthDocument

DEFAULT_GROUNDTRUTHS = Path("data/GroundTruths")
DEFAULT_PROFILE = Path("profiles/groundtruth-profile.json")
DEFAULT_SCHEMA = Path("schemas/groundtruth.schema.json")


def discover_groundtruth_profile(path: Path) -> dict[str, Any]:
    """Return content-free structural facts from every paired artifact."""

    if not path.is_dir():
        raise FileNotFoundError(f"GroundTruth directory is unavailable: {path.resolve()}")
    json_files = sorted(path.glob("*.parse.json"))
    markdown_files = sorted(path.glob("*.parse.md"))
    if not json_files:
        raise ValueError(f"no .parse.json files found in {path.resolve()}")
    if {item.stem for item in json_files} != {item.stem for item in markdown_files}:
        raise ValueError("GroundTruth JSON and Markdown filenames are not paired")

    top_level_orders: Counter[tuple[str, ...]] = Counter()
    metadata_orders: Counter[tuple[str, ...]] = Counter()
    element_orders: dict[str, Counter[tuple[str, ...]]] = defaultdict(Counter)
    element_counts: Counter[str] = Counter()
    page_count = 0
    page_breaks = 0

    for json_path in json_files:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        markdown_path = json_path.with_suffix(".md")
        paired_markdown = markdown_path.read_text(encoding="utf-8")
        if raw.get("markdown") != paired_markdown:
            raise ValueError(f"paired Markdown differs: {json_path.name}")
        document = GroundTruthDocument.model_validate(raw)
        top_level_orders[tuple(raw)] += 1
        metadata_orders[tuple(raw["metadata"])] += 1
        page_count += document.metadata.page_count
        page_breaks += document.markdown.count("<!-- PAGE BREAK -->")
        for page in raw["structure"]["children"]:
            element_orders["page"][tuple(page)] += 1
            for element in page["children"]:
                _record_element(element, element_counts, element_orders)

    return {
        "profile_version": 1,
        "knowledge_sources": [
            "knowledge/index.md",
            "knowledge/concepts/agentic-document-extraction.md",
            "knowledge/concepts/dpt-3.md",
            "knowledge/concepts/local-landingai-ground-truths.md",
        ],
        "pair_count": len(json_files),
        "source_page_count": page_count,
        "page_break_count": page_breaks,
        "top_level_key_orders": _counter_rows(top_level_orders),
        "metadata_key_orders": _counter_rows(metadata_orders),
        "element_counts": dict(sorted(element_counts.items())),
        "element_key_orders": {
            element_type: _counter_rows(orders)
            for element_type, orders in sorted(element_orders.items())
        },
        "filename_suffixes": [".parse.json", ".parse.md"],
        "range_units": "unicode_codepoints",
        "coordinate_system": "normalized_page_fraction_0_to_1",
        "markdown": {
            "page_break": "<!-- PAGE BREAK -->",
            "document_id": "<!-- doc_id=parse-<26-character-ulid> -->",
            "tables": "html",
            "encoding": "utf-8-no-bom",
            "line_endings": "lf",
            "terminal_newline": False,
        },
    }


def write_profile_artifacts(
    groundtruth_path: Path = DEFAULT_GROUNDTRUTHS,
    profile_path: Path = DEFAULT_PROFILE,
    schema_path: Path = DEFAULT_SCHEMA,
) -> None:
    profile = discover_groundtruth_profile(groundtruth_path)
    schema = GroundTruthDocument.model_json_schema()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(_json_text(profile), encoding="utf-8", newline="\n")
    schema_path.write_text(_json_text(schema), encoding="utf-8", newline="\n")


def check_profile_artifacts(
    groundtruth_path: Path = DEFAULT_GROUNDTRUTHS,
    profile_path: Path = DEFAULT_PROFILE,
    schema_path: Path = DEFAULT_SCHEMA,
) -> None:
    expected_profile = _json_text(discover_groundtruth_profile(groundtruth_path))
    expected_schema = _json_text(GroundTruthDocument.model_json_schema())
    if not profile_path.is_file() or profile_path.read_text(encoding="utf-8") != expected_profile:
        raise ValueError(f"GroundTruth profile is stale: {profile_path}")
    if not schema_path.is_file() or schema_path.read_text(encoding="utf-8") != expected_schema:
        raise ValueError(f"GroundTruth schema is stale: {schema_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("groundtruths", nargs="?", type=Path, default=DEFAULT_GROUNDTRUTHS)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    if arguments.check:
        check_profile_artifacts(arguments.groundtruths, arguments.profile, arguments.schema)
    else:
        write_profile_artifacts(arguments.groundtruths, arguments.profile, arguments.schema)


def _record_element(
    element: dict[str, Any],
    element_counts: Counter[str],
    element_orders: dict[str, Counter[tuple[str, ...]]],
) -> None:
    element_type = element["type"]
    element_counts[element_type] += 1
    element_orders[element_type][tuple(element)] += 1
    for child in element.get("children", []):
        _record_element(child, element_counts, element_orders)


def _counter_rows(counter: Counter[tuple[str, ...]]) -> list[dict[str, Any]]:
    return [
        {"keys": list(keys), "count": count}
        for keys, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


if __name__ == "__main__":
    main()
