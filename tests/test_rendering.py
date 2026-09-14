from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from ade_app.models import (
    Box,
    DraftLeaf,
    DraftTable,
    GroundTruthDocument,
    PageExtraction,
    SemanticCellLine,
    SemanticCheckbox,
    SemanticLeaf,
    SemanticLine,
    SemanticPageExtraction,
    SemanticTable,
    SemanticTableCell,
    SemanticText,
)
from ade_app.rendering import (
    PageOutcome,
    artifact_json,
    render_document,
    render_semantic_page,
)

JOB_ID = "parse-01m1be6gqfkfrk72qg4jf93fqz"


def _box(xmin: float, ymin: float, xmax: float, ymax: float) -> Box:
    return Box(xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax)


def test_semantic_page_renders_groundtruth_markdown_and_ranges_deterministically() -> None:
    heading_box = _box(0.1, 0.05, 0.9, 0.1)
    table_box = _box(0.1, 0.15, 0.9, 0.5)
    body_box = _box(0.1, 0.55, 0.9, 0.65)
    semantic = SemanticPageExtraction(
        children=[
            SemanticLeaf(
                type="text",
                lines=[
                    SemanticLine(
                        content=[SemanticText(text="Form")],
                        box=heading_box,
                        style="heading_1",
                    )
                ],
                box=heading_box,
            ),
            SemanticTable(
                box=table_box,
                children=[
                    SemanticTableCell(
                        row=1,
                        col=1,
                        lines=[
                            SemanticCellLine(
                                content=[
                                    SemanticCheckbox(checked=False),
                                    SemanticText(text=" Other"),
                                ]
                            )
                        ],
                        box=_box(0.5, 0.3, 0.9, 0.4),
                    ),
                    SemanticTableCell(
                        row=0,
                        col=0,
                        colspan=2,
                        lines=[SemanticCellLine(content=[SemanticText(text="Member & details")])],
                        box=_box(0.1, 0.15, 0.9, 0.3),
                    ),
                    SemanticTableCell(
                        row=1,
                        col=0,
                        lines=[
                            SemanticCellLine(
                                content=[
                                    SemanticCheckbox(checked=True),
                                    SemanticText(text=" Participating"),
                                ]
                            )
                        ],
                        box=_box(0.1, 0.3, 0.5, 0.4),
                    ),
                ],
            ),
            SemanticLeaf(
                type="text",
                lines=[
                    SemanticLine(
                        content=[
                            SemanticText(text="Café "),
                            SemanticText(text="😀", style="strong"),
                        ],
                        box=body_box,
                    )
                ],
                box=body_box,
            ),
        ]
    )

    first = render_semantic_page(semantic)
    second = render_semantic_page(semantic)

    assert first == second
    assert first.markdown == (
        "# Form\n\n\n"
        "<table>\n"
        '<tr><td colspan="2">Member &amp; details</td></tr>\n'
        "<tr><td>[x] Participating</td><td>[ ] Other</td></tr>\n"
        "</table>\n\n"
        "Café **😀**\n"
    )
    table = first.children[1]
    assert isinstance(table, DraftTable)
    assert first.markdown[table.grounding.range.start : table.grounding.range.end].startswith(
        "<table>"
    )
    assert [
        first.markdown[cell.grounding.range.start : cell.grounding.range.end]
        for cell in table.children
    ] == ["Member &amp; details", "[x] Participating", "[ ] Other"]
    assert all(not cell.atomic_grounding for cell in table.children)
    body = first.children[2]
    assert isinstance(body, DraftLeaf)
    atomic = body.atomic_grounding[0].range
    assert first.markdown[atomic.start : atomic.end] == "Café **😀**"


def test_semantic_leaf_supports_heading_and_body_lines_in_one_element() -> None:
    box = _box(0.1, 0.1, 0.9, 0.3)
    result = render_semantic_page(
        SemanticPageExtraction(
            children=[
                SemanticLeaf(
                    type="text",
                    lines=[
                        SemanticLine(
                            content=[SemanticText(text="Title")],
                            box=box,
                            style="heading_1",
                        ),
                        SemanticLine(content=[SemanticText(text="Body")], box=box),
                    ],
                    box=box,
                )
            ]
        )
    )

    assert result.markdown == "# Title\nBody\n"


def test_semantic_table_uses_html_inline_styles() -> None:
    box = _box(0.1, 0.1, 0.9, 0.3)
    result = render_semantic_page(
        SemanticPageExtraction(
            children=[
                SemanticTable(
                    box=box,
                    children=[
                        SemanticTableCell(
                            row=0,
                            col=0,
                            lines=[
                                SemanticCellLine(
                                    content=[
                                        SemanticText(text="Label", style="strong"),
                                        SemanticText(text=" note", style="emphasis"),
                                    ]
                                )
                            ],
                            box=box,
                        )
                    ],
                )
            ]
        )
    )

    assert "<td><strong>Label</strong><em> note</em></td>" in result.markdown
    assert len(result.children) == 1


def test_semantic_table_can_preserve_structural_cells_without_markdown_cells() -> None:
    box = _box(0.1, 0.1, 0.9, 0.3)
    result = render_semantic_page(
        SemanticPageExtraction(
            children=[
                SemanticTable(
                    box=box,
                    children=[
                        SemanticTableCell(
                            row=0,
                            col=0,
                            render_in_markdown=False,
                            box=box,
                        )
                    ],
                )
            ]
        )
    )

    assert result.markdown == "<table></table>"
    table = result.children[0]
    assert isinstance(table, DraftTable)
    assert len(table.children) == 1
    assert table.children[0].grounding.range.start == table.grounding.range.start
    assert table.children[0].grounding.range.end == table.grounding.range.start


def test_semantic_table_can_omit_one_structural_cell_from_markdown() -> None:
    box = _box(0.1, 0.1, 0.9, 0.3)
    result = render_semantic_page(
        SemanticPageExtraction(
            children=[
                SemanticTable(
                    box=box,
                    children=[
                        SemanticTableCell(
                            row=0,
                            col=0,
                            lines=[SemanticCellLine(content=[SemanticText(text="Visible")])],
                            box=box,
                        ),
                        SemanticTableCell(
                            row=0,
                            col=1,
                            render_in_markdown=False,
                            box=box,
                        ),
                    ],
                )
            ]
        )
    )

    assert result.markdown.count("<td") == 1
    table = result.children[0]
    assert isinstance(table, DraftTable)
    assert len(table.children) == 2
    assert table.children[1].grounding.range.start == table.grounding.range.start
    assert table.children[1].grounding.range.end == table.grounding.range.start


def test_heading_does_not_duplicate_inline_emphasis() -> None:
    box = _box(0.1, 0.1, 0.9, 0.2)
    result = render_semantic_page(
        SemanticPageExtraction(
            children=[
                SemanticLeaf(
                    type="text",
                    lines=[
                        SemanticLine(
                            content=[SemanticText(text="Title", style="strong")],
                            box=box,
                            style="heading_1",
                        )
                    ],
                    box=box,
                )
            ]
        )
    )

    assert result.markdown == "# Title\n"


def test_semantic_table_renders_before_quality_checks_overlapping_cells() -> None:
    box = _box(0.1, 0.1, 0.9, 0.9)
    result = render_semantic_page(
        SemanticPageExtraction(
            children=[
                SemanticTable(
                    box=box,
                    children=[
                        SemanticTableCell(row=0, col=0, colspan=2, box=box),
                        SemanticTableCell(row=0, col=1, box=box),
                    ],
                )
            ]
        )
    )

    assert result.markdown.count("<td") == 2


def test_render_document_reproduces_markers_and_rebases_ranges(
    sample_page: PageExtraction,
) -> None:
    second = sample_page.model_copy(deep=True, update={"markdown": "Second page"})
    second_element = second.children[0]
    assert isinstance(second_element, DraftLeaf)
    second_element.grounding.range.end = len(second.markdown)
    second_element.atomic_grounding[0].range.end = len(second.markdown)
    artifact = render_document(
        page_count=5,
        outcomes=[
            PageOutcome(source_page=1, extraction=sample_page),
            PageOutcome(source_page=3, extraction=second),
        ],
        duration_ms=25,
        cost_usd=Decimal("0.0012"),
        job_id=JOB_ID,
    )

    assert artifact.markdown == (
        f"# Sample heading\n\n<!-- PAGE BREAK -->\n\nSecond page\n\n<!-- doc_id={JOB_ID} -->"
    )
    assert [page.grounding.page for page in artifact.structure.children] == [1, 3]
    assert artifact.structure.children[0].children[0].id == "text-0"
    assert artifact.structure.children[1].children[0].id == "text-1"
    assert artifact.metadata.page_count == 5
    assert artifact.metadata.output_markdown_chars == len(artifact.markdown)
    assert not artifact_json(artifact).endswith("\n")


def test_failed_page_is_preserved(sample_page: PageExtraction) -> None:
    artifact = render_document(
        page_count=2,
        outcomes=[
            PageOutcome(source_page=1, extraction=sample_page),
            PageOutcome(source_page=2, failure_reason="TimeoutError: page extraction failed"),
        ],
        duration_ms=10,
        cost_usd=Decimal("0"),
        job_id=JOB_ID,
    )
    failed = artifact.structure.children[1]
    assert artifact.metadata.failed_pages == [2]
    assert failed.status == "failed"
    assert failed.grounding.range.start == failed.grounding.range.end


def test_document_rejects_table_cell_atomic_grounding_from_another_page() -> None:
    box = _box(0.1, 0.1, 0.9, 0.3)
    page = render_semantic_page(
        SemanticPageExtraction(
            children=[
                SemanticTable(
                    box=box,
                    children=[
                        SemanticTableCell(
                            row=0,
                            col=0,
                            lines=[SemanticCellLine(content=[SemanticText(text="Value")])],
                            box=box,
                        )
                    ],
                )
            ]
        )
    )
    artifact = render_document(
        page_count=1,
        outcomes=[PageOutcome(source_page=1, extraction=page)],
        duration_ms=1,
        cost_usd=Decimal(0),
        job_id=JOB_ID,
    )
    payload = artifact.model_dump(mode="json")
    cell = payload["structure"]["children"][0]["children"][0]["children"][0]
    cell["atomic_grounding"] = [
        {
            "page": 2,
            "range": dict(cell["grounding"]["range"]),
            "box": dict(cell["grounding"]["box"]),
        }
    ]

    with pytest.raises(ValidationError, match="atomic grounding page"):
        GroundTruthDocument.model_validate(payload)
