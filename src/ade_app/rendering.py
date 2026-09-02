"""Render validated page drafts into GroundTruth-compatible artifacts."""

from __future__ import annotations

import secrets
import time
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from html import escape

from ade_app.constants import GROUNDTRUTH_OPENAPI_SPEC, MODEL_ID, PAGE_BREAK
from ade_app.models import (
    Billing,
    Box,
    DocumentNode,
    DraftGrounding,
    DraftLeaf,
    DraftTable,
    DraftTableCell,
    Grounding,
    GroundTruthDocument,
    LeafElement,
    PageExtraction,
    PageNode,
    ParseMetadata,
    SemanticCellLine,
    SemanticCheckbox,
    SemanticElement,
    SemanticFigure,
    SemanticLeaf,
    SemanticLine,
    SemanticPageExtraction,
    SemanticTable,
    SemanticTableCell,
    SemanticText,
    TableCellElement,
    TableElement,
    TextRange,
)

CROCKFORD_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"


@dataclass(frozen=True, slots=True)
class PageOutcome:
    source_page: int
    extraction: PageExtraction | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if self.source_page < 1:
            raise ValueError("source_page must be positive")
        if (self.extraction is None) == (self.failure_reason is None):
            raise ValueError("page outcome requires exactly one result or failure")


def generate_job_id() -> str:
    """Generate the observed ``parse-<ULID>`` identifier without another dependency."""

    value = ((time.time_ns() // 1_000_000) << 80) | secrets.randbits(80)
    encoded = "".join(CROCKFORD_ALPHABET[(value >> shift) & 31] for shift in range(125, -1, -5))
    return f"parse-{encoded}"


def render_semantic_page(page: SemanticPageExtraction) -> PageExtraction:
    """Render semantic blocks into deterministic GroundTruth-style Markdown and ranges."""

    chunks: list[str] = []
    children: list[DraftLeaf | DraftTable] = []
    for index, element in enumerate(page.children):
        if index:
            chunks.append("\n\n")
        start = sum(len(chunk) for chunk in chunks)
        markdown, rendered = _render_semantic_element(element, start)
        chunks.append(markdown)
        children.append(rendered)
    return PageExtraction(markdown="".join(chunks), children=children)


def render_document(
    *,
    page_count: int,
    outcomes: list[PageOutcome],
    duration_ms: int,
    cost_usd: Decimal,
    job_id: str | None = None,
    service_tier: str = "standard",
    model_version: str = MODEL_ID,
) -> GroundTruthDocument:
    """Assemble pages, rebase ranges, assign IDs, and validate the final artifact."""

    if not outcomes:
        raise ValueError("at least one page outcome is required")
    document_id = job_id or generate_job_id()
    chunks: list[str] = []
    offset = 0
    counters: Counter[str] = Counter()
    pages: list[PageNode] = []

    for index, outcome in enumerate(outcomes):
        page_start = offset
        if outcome.extraction is None:
            pages.append(
                PageNode(
                    grounding=_page_grounding(outcome.source_page, page_start, page_start),
                    status="failed",
                    reason=outcome.failure_reason,
                    children=[],
                )
            )
        else:
            markdown = outcome.extraction.markdown
            chunks.append(markdown)
            offset += len(markdown)
            children = [
                _materialize_element(element, outcome.source_page, page_start, counters)
                for element in outcome.extraction.children
            ]
            chunks.append("\n\n")
            offset += 2
            pages.append(
                PageNode(
                    grounding=_page_grounding(outcome.source_page, page_start, offset),
                    status="ok",
                    children=children,
                )
            )

        if index < len(outcomes) - 1:
            chunks.append(f"{PAGE_BREAK}\n\n")
            offset += len(PAGE_BREAK) + 2

    chunks.append(f"<!-- doc_id={document_id} -->")
    markdown = "".join(chunks)
    failed_pages = [outcome.source_page for outcome in outcomes if outcome.extraction is None]
    artifact = GroundTruthDocument(
        markdown=markdown,
        metadata=ParseMetadata(
            job_id=document_id,
            model_version=model_version,
            page_count=page_count,
            output_markdown_chars=len(markdown),
            openapi_spec=GROUNDTRUTH_OPENAPI_SPEC,
            failed_pages=failed_pages,
            duration_ms=duration_ms,
            billing=Billing(
                service_tier="priority" if service_tier == "priority" else "standard",
                total_credits=float(cost_usd),
            ),
        ),
        structure=DocumentNode(children=pages),
    )
    return artifact


def artifact_json(artifact: GroundTruthDocument) -> str:
    """Serialize using the observed two-space JSON style and no terminal newline."""

    return artifact.model_dump_json(indent=2, exclude_none=True)


def _page_grounding(page: int, start: int, end: int) -> Grounding:
    return Grounding(
        page=page,
        range=TextRange(start=start, end=end),
        box=Box(xmin=0.0, ymin=0.0, xmax=1.0, ymax=1.0),
    )


def _render_semantic_element(
    element: SemanticElement, start: int
) -> tuple[str, DraftLeaf | DraftTable]:
    if isinstance(element, SemanticTable):
        return _render_semantic_table(element, start)
    if isinstance(element, SemanticFigure):
        return _render_semantic_figure(element, start)
    return _render_semantic_leaf(element, start)


def _render_semantic_leaf(element: SemanticLeaf, start: int) -> tuple[str, DraftLeaf]:
    parts: list[str] = []
    atomics: list[DraftGrounding] = []
    for index, line in enumerate(element.lines):
        if index:
            parts.append("\n")
        line_start = start + sum(len(part) for part in parts)
        heading = {
            "plain": "",
            "heading_1": "# ",
            "heading_2": "## ",
            "heading_3": "### ",
        }[line.style]
        rendered = heading + _render_line(line, heading_context=bool(heading))
        parts.append(rendered)
        atomics.append(
            DraftGrounding(
                range=TextRange(start=line_start, end=line_start + len(rendered)),
                box=line.box,
            )
        )
    parts.append("\n")
    markdown = "".join(parts)
    return markdown, DraftLeaf(
        type=element.type,
        grounding=DraftGrounding(
            range=TextRange(start=start, end=start + len(markdown)),
            box=element.box,
        ),
        atomic_grounding=atomics,
    )


def _render_semantic_figure(element: SemanticFigure, start: int) -> tuple[str, DraftLeaf]:
    figure_type = escape(element.figure_type, quote=True)
    prefix = f'<figure type="{figure_type}">\n<description>'
    parts = [prefix]
    atomics: list[DraftGrounding] = []
    description_start = start + len(prefix)
    description = _render_line(element.description, html_context=True)
    parts.extend((description, "</description>"))
    atomics.append(
        DraftGrounding(
            range=TextRange(
                start=description_start,
                end=description_start + len(description),
            ),
            box=element.description.box,
        )
    )
    for line in element.lines:
        parts.append("\n")
        line_start = start + sum(len(part) for part in parts)
        rendered = _render_line(line, html_context=True)
        parts.append(rendered)
        atomics.append(
            DraftGrounding(
                range=TextRange(start=line_start, end=line_start + len(rendered)),
                box=line.box,
            )
        )
    parts.extend(("\n</figure>", "\n"))
    markdown = "".join(parts)
    return markdown, DraftLeaf(
        type="figure",
        grounding=DraftGrounding(
            range=TextRange(start=start, end=start + len(markdown)),
            box=element.box,
        ),
        atomic_grounding=atomics,
    )


def _render_semantic_table(element: SemanticTable, start: int) -> tuple[str, DraftTable]:
    ordered = sorted(element.children, key=lambda cell: (cell.row, cell.col))
    visible = [cell for cell in ordered if cell.render_in_markdown]
    if not visible:
        markdown = "<table></table>"
        return markdown, DraftTable(
            grounding=DraftGrounding(
                range=TextRange(start=start, end=start + len(markdown)),
                box=element.box,
            ),
            children=[
                DraftTableCell(
                    grounding=DraftGrounding(
                        range=TextRange(start=start, end=start),
                        box=cell.box,
                    ),
                    atomic_grounding=[],
                    row=cell.row,
                    col=cell.col,
                    colspan=cell.colspan,
                    rowspan=cell.rowspan,
                )
                for cell in ordered
            ],
        )

    parts = ["<table>\n"]
    rendered_cells: dict[int, DraftTableCell] = {}
    rows: dict[int, list[SemanticTableCell]] = {}
    for cell in visible:
        rows.setdefault(cell.row, []).append(cell)
    for row in sorted(rows):
        parts.append("<tr>")
        for cell in rows[row]:
            attributes = ""
            if cell.colspan > 1:
                attributes += f' colspan="{cell.colspan}"'
            if cell.rowspan > 1:
                attributes += f' rowspan="{cell.rowspan}"'
            parts.append(f"<td{attributes}>")
            cell_start = start + sum(len(part) for part in parts)
            cell_text = "<br>".join(_render_line(line, html_context=True) for line in cell.lines)
            parts.append(cell_text)
            cell_end = cell_start + len(cell_text)
            parts.append("</td>")
            rendered_cells[id(cell)] = DraftTableCell(
                grounding=DraftGrounding(
                    range=TextRange(start=cell_start, end=cell_end),
                    box=cell.box,
                ),
                atomic_grounding=[],
                row=cell.row,
                col=cell.col,
                colspan=cell.colspan,
                rowspan=cell.rowspan,
            )
        parts.append("</tr>\n")
    parts.append("</table>")
    markdown = "".join(parts)
    cells = [
        rendered_cells.get(id(cell))
        or DraftTableCell(
            grounding=DraftGrounding(
                range=TextRange(start=start, end=start),
                box=cell.box,
            ),
            atomic_grounding=[],
            row=cell.row,
            col=cell.col,
            colspan=cell.colspan,
            rowspan=cell.rowspan,
        )
        for cell in ordered
    ]
    return markdown, DraftTable(
        grounding=DraftGrounding(
            range=TextRange(start=start, end=start + len(markdown)),
            box=element.box,
        ),
        children=cells,
    )


def _render_line(
    line: SemanticLine | SemanticCellLine,
    *,
    html_context: bool = False,
    heading_context: bool = False,
) -> str:
    return "".join(
        _render_inline(item, html_context=html_context, heading_context=heading_context)
        for item in line.content
    )


def _render_inline(
    item: SemanticText | SemanticCheckbox, *, html_context: bool, heading_context: bool = False
) -> str:
    if isinstance(item, SemanticCheckbox):
        return "[x]" if item.checked else "[ ]"
    text = escape(item.text, quote=False) if html_context else item.text
    if item.style == "strong" and not heading_context:
        if html_context:
            return f"<strong>{text}</strong>"
        return f"**{text}**"
    if item.style == "emphasis" and not heading_context:
        if html_context:
            return f"<em>{text}</em>"
        return f"*{text}*"
    return text


def _materialize_element(
    element: DraftLeaf | DraftTable,
    page: int,
    page_offset: int,
    counters: Counter[str],
) -> LeafElement | TableElement:
    element_id = _next_id(element.type, counters)
    grounding = _materialize_grounding(element.grounding, page, page_offset)
    if isinstance(element, DraftTable):
        cells = [
            TableCellElement(
                id=_next_id("table_cell", counters),
                grounding=_materialize_grounding(cell.grounding, page, page_offset),
                atomic_grounding=[
                    _materialize_grounding(atomic, page, page_offset)
                    for atomic in cell.atomic_grounding
                ],
                row=cell.row,
                col=cell.col,
                colspan=cell.colspan,
                rowspan=cell.rowspan,
            )
            for cell in element.children
        ]
        return TableElement(id=element_id, grounding=grounding, children=cells)
    return LeafElement(
        type=element.type,
        id=element_id,
        grounding=grounding,
        atomic_grounding=[
            _materialize_grounding(atomic, page, page_offset) for atomic in element.atomic_grounding
        ],
    )


def _materialize_grounding(value: DraftGrounding, page: int, offset: int) -> Grounding:
    return Grounding(
        page=page,
        range=TextRange(
            start=value.range.start + offset,
            end=value.range.end + offset,
        ),
        box=value.box,
    )


def _next_id(element_type: str, counters: Counter[str]) -> str:
    element_id = f"{element_type}-{counters[element_type]}"
    counters[element_type] += 1
    return element_id
