"""Deterministic metrics shared by evaluation and quality calibration."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from decimal import Decimal
from difflib import SequenceMatcher
from html.parser import HTMLParser
from typing import Any

from ade_app.models import GroundTruthDocument, PageNode, TableElement

DOC_ID_RE = re.compile(r"\n*<!-- doc_id=[^>]+ -->\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[dict[str, Any]]]] = []
        self._table: list[list[dict[str, Any]]] | None = None
        self._row: list[dict[str, Any]] | None = None
        self._cell: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = {
                "text": "",
                "rowspan": int(values.get("rowspan") or 1),
                "colspan": int(values.get("colspan") or 1),
            }

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._cell["text"] = inline_text(self._cell["text"])
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def normalize_markdown(value: str) -> str:
    """Normalize transport whitespace while preserving Markdown/HTML structure and values."""

    text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    text = DOC_ID_RE.sub("", text)
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def character_error_rate(reference: str, candidate: str) -> tuple[int, float, float]:
    reference, candidate = normalize_markdown(reference), normalize_markdown(candidate)
    if reference == candidate:
        return 0, 0.0, 1.0
    distance = _levenshtein(reference, candidate)
    denominator = max(1, len(reference))
    rate = distance / denominator
    return distance, rate, max(0.0, 1.0 - rate)


def evaluate_page(
    gt_doc: GroundTruthDocument,
    generated_doc: GroundTruthDocument,
    gt_page: PageNode,
    generated_page: PageNode | None,
) -> dict[str, Any]:
    page_number = gt_page.grounding.page
    gt_markdown = page_markdown(gt_doc, gt_page)
    if generated_page is None or generated_page.status == "failed":
        return {
            "page": page_number,
            "status": "failed",
            "failure_reason": generated_page.reason if generated_page else "page missing",
            "reference_elements": len(gt_page.children),
            "candidate_elements": 0,
            "matched_elements": 0,
            "markdown_reference_chars": len(normalize_markdown(gt_markdown)),
            "markdown_edits": len(normalize_markdown(gt_markdown)),
            "field_exact": precision_recall_f1(0, 0, len(gt_page.children)),
            "field_normalized": precision_recall_f1(0, 0, len(gt_page.children)),
            "headings": precision_recall_f1(0, 0, len(headings(gt_markdown))),
            "tables": precision_recall_f1(0, 0, len(tables(gt_markdown))),
            "table_cell_values": precision_recall_f1(0, 0, len(table_cells(gt_markdown))),
        }
    generated_markdown = page_markdown(generated_doc, generated_page)
    matches = align_elements(gt_doc, generated_doc, gt_page, generated_page)
    edits, cer, similarity = character_error_rate(gt_markdown, generated_markdown)
    gt_headings, candidate_headings = headings(gt_markdown), headings(generated_markdown)
    heading_matches = lcs_length(gt_headings, candidate_headings)
    gt_tables, candidate_tables = tables(gt_markdown), tables(generated_markdown)
    table_matches = lcs_length(
        [table_shape(table) for table in gt_tables],
        [table_shape(table) for table in candidate_tables],
    )
    gt_cells = [cell["text"] for table in gt_tables for row in table for cell in row]
    candidate_cells = [
        cell["text"] for table in candidate_tables for row in table for cell in row
    ]
    cell_matches = lcs_length(gt_cells, candidate_cells)
    cell_edits, cell_cer, _ = character_error_rate(
        "\n".join(gt_cells), "\n".join(candidate_cells)
    )
    exact_fields = sum(
        element_text(gt_doc, gt_page.children[left])
        == element_text(generated_doc, generated_page.children[right])
        for left, right in matches
    )
    normalized_fields = sum(
        normalize_markdown(element_text(gt_doc, gt_page.children[left]))
        == normalize_markdown(element_text(generated_doc, generated_page.children[right]))
        for left, right in matches
    )
    types = Counter(element.type for element in gt_page.children)
    candidate_types = Counter(element.type for element in generated_page.children)
    matched_types = Counter(gt_page.children[left].type for left, _ in matches)
    box_ious = [
        box_iou(
            gt_page.children[left].grounding.box,
            generated_page.children[right].grounding.box,
        )
        for left, right in matches
    ]
    return {
        "page": page_number,
        "status": "ok",
        "reference_elements": len(gt_page.children),
        "candidate_elements": len(generated_page.children),
        "matched_elements": len(matches),
        "element_prf": precision_recall_f1(
            len(matches), len(generated_page.children), len(gt_page.children)
        ),
        "element_prf_by_type": {
            kind: precision_recall_f1(matched_types[kind], candidate_types[kind], types[kind])
            for kind in sorted(types.keys() | candidate_types.keys())
        },
        "field_exact": precision_recall_f1(
            exact_fields, len(generated_page.children), len(gt_page.children)
        ),
        "field_normalized": precision_recall_f1(
            normalized_fields, len(generated_page.children), len(gt_page.children)
        ),
        "field_agreement": field_agreement(gt_page, generated_page, matches, box_ious),
        "markdown_reference_chars": len(normalize_markdown(gt_markdown)),
        "markdown_edits": edits,
        "markdown_cer": cer,
        "markdown_similarity": similarity,
        "headings": precision_recall_f1(
            heading_matches, len(candidate_headings), len(gt_headings)
        ),
        "tables": precision_recall_f1(table_matches, len(candidate_tables), len(gt_tables)),
        "table_cell_values": {
            **precision_recall_f1(cell_matches, len(candidate_cells), len(gt_cells)),
            "character_edits": cell_edits,
            "cer": cell_cer,
        },
    }


def align_elements(
    gt_doc: GroundTruthDocument,
    candidate_doc: GroundTruthDocument,
    gt_page: PageNode,
    candidate_page: PageNode,
) -> list[tuple[int, int]]:
    left, right = gt_page.children, candidate_page.children
    scores = [[0.0] * (len(right) + 1) for _ in range(len(left) + 1)]
    choices = [[""] * (len(right) + 1) for _ in range(len(left) + 1)]
    for i in range(1, len(left) + 1):
        for j in range(1, len(right) + 1):
            match_score = -1.0
            if left[i - 1].type == right[j - 1].type:
                a = element_text(gt_doc, left[i - 1])
                b = element_text(candidate_doc, right[j - 1])
                text_score = SequenceMatcher(None, inline_text(a), inline_text(b)).ratio()
                spatial = box_iou(left[i - 1].grounding.box, right[j - 1].grounding.box)
                combined = (0.8 * text_score + 0.2 * spatial) if a or b else spatial
                match_score = scores[i - 1][j - 1] + combined - 0.5
            options = [
                (scores[i - 1][j], "up"),
                (scores[i][j - 1], "left"),
                (match_score, "match"),
            ]
            scores[i][j], choices[i][j] = max(
                options, key=lambda item: (item[0], item[1] == "match")
            )
    matches: list[tuple[int, int]] = []
    i, j = len(left), len(right)
    while i and j:
        choice = choices[i][j]
        if choice == "match":
            matches.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif choice == "up":
            i -= 1
        else:
            j -= 1
    return list(reversed(matches))


def field_agreement(
    gt_page: PageNode,
    candidate_page: PageNode,
    matches: list[tuple[int, int]],
    box_ious: list[float],
) -> dict[str, Any]:
    total = len(matches)
    exact_ids = atomic_counts = 0
    coordinate_error = 0.0
    table_cells_reference = table_cells_candidate = table_cells_exact = 0
    for left, right in matches:
        expected, candidate = gt_page.children[left], candidate_page.children[right]
        exact_ids += expected.id == candidate.id
        expected_atomic = [] if isinstance(expected, TableElement) else expected.atomic_grounding
        candidate_atomic = [] if isinstance(candidate, TableElement) else candidate.atomic_grounding
        atomic_counts += len(expected_atomic) == len(candidate_atomic)
        coordinate_error += (
            sum(
                abs(a - b)
                for a, b in zip(
                    expected.grounding.box.model_dump().values(),
                    candidate.grounding.box.model_dump().values(),
                    strict=True,
                )
            )
            / 4
        )
        if isinstance(expected, TableElement) and isinstance(candidate, TableElement):
            table_cells_reference += len(expected.children)
            table_cells_candidate += len(candidate.children)
            for expected_cell, candidate_cell in zip(
                expected.children, candidate.children, strict=False
            ):
                table_cells_exact += (
                    expected_cell.row,
                    expected_cell.col,
                    expected_cell.rowspan,
                    expected_cell.colspan,
                ) == (
                    candidate_cell.row,
                    candidate_cell.col,
                    candidate_cell.rowspan,
                    candidate_cell.colspan,
                )
    return {
        "matched_count": total,
        "id_exact_rate": exact_ids / total if total else None,
        "atomic_count_exact_rate": atomic_counts / total if total else None,
        "mean_box_iou": sum(box_ious) / total if total else None,
        "mean_box_coordinate_absolute_error": coordinate_error / total if total else None,
        "table_cells": precision_recall_f1(
            table_cells_exact, table_cells_candidate, table_cells_reference
        ),
    }


def aggregate_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
    if not pages:
        return {
            "page_success_rate": 0.0,
            "element_prf_micro": precision_recall_f1(0, 0, 0),
            "field_exact_prf_micro": precision_recall_f1(0, 0, 0),
            "field_normalized_prf_micro": precision_recall_f1(0, 0, 0),
            "markdown_cer_micro": 0.0,
            "markdown_similarity_micro": 0.0,
            "heading_prf_micro": precision_recall_f1(0, 0, 0),
            "table_prf_micro": precision_recall_f1(0, 0, 0),
            "table_cell_value_prf_micro": precision_recall_f1(0, 0, 0),
        }
    reference = sum(page.get("reference_elements", 0) for page in pages)
    candidate = sum(page.get("candidate_elements", 0) for page in pages)
    matched = sum(page.get("matched_elements", 0) for page in pages)
    reference_chars = sum(page.get("markdown_reference_chars", 0) for page in pages)
    edits = sum(page.get("markdown_edits", 0) for page in pages)
    heading = _sum_precision_recall_f1(pages, "headings")
    table_values = _sum_precision_recall_f1(pages, "tables")
    fields_exact = _sum_precision_recall_f1(pages, "field_exact")
    fields_normalized = _sum_precision_recall_f1(pages, "field_normalized")
    table_cell_values = _sum_precision_recall_f1(pages, "table_cell_values")
    return {
        "page_success_rate": sum(page["status"] == "ok" for page in pages) / len(pages),
        "element_prf_micro": precision_recall_f1(matched, candidate, reference),
        "field_exact_prf_micro": fields_exact,
        "field_normalized_prf_micro": fields_normalized,
        "markdown_cer_micro": edits / max(1, reference_chars),
        "markdown_similarity_micro": max(0.0, 1 - edits / max(1, reference_chars)),
        "heading_prf_micro": heading,
        "table_prf_micro": table_values,
        "table_cell_value_prf_micro": table_cell_values,
    }


def aggregate_documents(documents: list[dict[str, Any]]) -> dict[str, Any]:
    pages = [page for document in documents for page in document.get("pages", [])]
    usage_fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_tokens",
        "output_tokens",
        "reasoning_tokens",
    )
    return {
        "valid_json_rate": (
            sum(bool(document.get("valid_json")) for document in documents) / len(documents)
            if documents
            else 0.0
        ),
        **aggregate_pages(pages),
        "usage": {
            field: sum(document.get("usage", {}).get(field, 0) for document in documents)
            for field in usage_fields
        },
        "estimated_cost_usd": str(
            sum(
                (Decimal(str(document.get("estimated_cost_usd", 0))) for document in documents),
                Decimal(0),
            )
        ),
        "elapsed_ms": sum(int(document.get("elapsed_ms", 0)) for document in documents),
        "api_call_count": sum(int(page.get("api_call_count", 0)) for page in pages),
        "routing_call_count": sum(int(page.get("routing_call_count", 0)) for page in pages),
        "fallback_page_count": sum(int(page.get("routing_call_count", 0)) > 0 for page in pages),
        "retry_count": sum(int(page.get("retry_count", 0)) for page in pages),
        "failed_page_count": sum(page.get("status") != "ok" for page in pages),
        "usage_complete": all(
            page.get("status") == "ok"
            and all(
                field in page
                for field in ("api_call_count", "routing_call_count", "retry_count")
            )
            and isinstance(page.get("usage"), dict)
            and all(field in page["usage"] for field in usage_fields)
            for page in pages
        ),
    }


def page_markdown(document: GroundTruthDocument, page: PageNode) -> str:
    return document.markdown[page.grounding.range.start : page.grounding.range.end]


def element_text(document: GroundTruthDocument, element: Any) -> str:
    value = element.grounding.range
    return document.markdown[value.start : value.end]


def headings(markdown: str) -> list[tuple[int, str]]:
    result = []
    for line in normalize_markdown(markdown).splitlines():
        match = HEADING_RE.match(line)
        if match:
            result.append((len(match.group(1)), inline_text(match.group(2))))
    return result


def tables(markdown: str) -> list[list[list[dict[str, Any]]]]:
    parser = _TableParser()
    parser.feed(normalize_markdown(markdown))
    return parser.tables


def table_cells(markdown: str) -> list[str]:
    return [cell["text"] for table in tables(markdown) for row in table for cell in row]


def table_shape(
    table: list[list[dict[str, Any]]],
) -> tuple[tuple[tuple[int, int], ...], ...]:
    return tuple(
        tuple((int(cell["rowspan"]), int(cell["colspan"])) for cell in row) for row in table
    )


def box_iou(left: Any, right: Any) -> float:
    width = max(0.0, min(left.xmax, right.xmax) - max(left.xmin, right.xmin))
    height = max(0.0, min(left.ymax, right.ymax) - max(left.ymin, right.ymin))
    intersection = width * height
    left_area = (left.xmax - left.xmin) * (left.ymax - left.ymin)
    right_area = (right.xmax - right.xmin) * (right.ymax - right.ymin)
    return intersection / max(1e-12, left_area + right_area - intersection)


def precision_recall_f1(matched: int, candidate: int, reference: int) -> dict[str, Any]:
    precision = matched / candidate if candidate else (1.0 if reference == 0 else 0.0)
    recall = matched / reference if reference else (1.0 if candidate == 0 else 0.0)
    return {
        "matched": matched,
        "candidate": candidate,
        "reference": reference,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
    }


def inline_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def _sum_precision_recall_f1(
    pages: list[dict[str, Any]], key: str
) -> dict[str, Any]:
    return precision_recall_f1(
        sum(page.get(key, {}).get("matched", 0) for page in pages),
        sum(page.get(key, {}).get("candidate", 0) for page in pages),
        sum(page.get(key, {}).get("reference", 0) for page in pages),
    )


def _levenshtein(left: str, right: str) -> int:
    prefix = 0
    for left_char, right_char in zip(left, right, strict=False):
        if left_char != right_char:
            break
        prefix += 1
    if prefix:
        left, right = left[prefix:], right[prefix:]

    suffix = 0
    for left_char, right_char in zip(reversed(left), reversed(right), strict=False):
        if left_char != right_char:
            break
        suffix += 1
    if suffix:
        left, right = left[:-suffix], right[:-suffix]

    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def lcs_length(left: list[Any], right: list[Any]) -> int:
    previous = [0] * (len(right) + 1)
    for left_value in left:
        current = [0]
        for column, right_value in enumerate(right, start=1):
            current.append(
                previous[column - 1] + 1
                if left_value == right_value
                else max(previous[column], current[-1])
            )
        previous = current
    return previous[-1]
