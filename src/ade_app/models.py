"""Strict schemas for model drafts and GroundTruth-compatible artifacts."""

from __future__ import annotations

import re
from collections import Counter
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

LeafType = Literal[
    "text",
    "figure",
    "logo",
    "marginalia",
    "attestation",
    "scan_code",
]
ElementType = Literal[
    "text",
    "table",
    "table_cell",
    "figure",
    "logo",
    "marginalia",
    "attestation",
    "scan_code",
]

ID_PATTERN = re.compile(
    r"^(text|table|table_cell|figure|logo|marginalia|attestation|scan_code)-(0|[1-9]\d*)$"
)
JOB_ID_PATTERN = re.compile(r"^parse-[0-9a-hjkmnp-tv-z]{26}$")

# Model-facing collection limits bound malformed or adversarial structured output.
# They are deliberately above the largest observed GroundTruth structures.
MAX_PAGE_ELEMENTS = 64
MAX_TABLE_CELLS = 128
MAX_LINES_PER_ELEMENT = 128
MAX_INLINE_RUNS_PER_LINE = 128
MAX_AUDIT_FINDINGS_PER_SEGMENT = 32
MAX_FIELD_RESOLUTIONS_PER_SEGMENT = 64


class StrictModel(BaseModel):
    """Base model that rejects coercion and unknown fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


class TextRange(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end < self.start:
            raise ValueError("range end must be greater than or equal to start")
        return self


class Box(StrictModel):
    xmin: float = Field(ge=0, le=1)
    ymin: float = Field(ge=0, le=1)
    xmax: float = Field(ge=0, le=1)
    ymax: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_edges(self) -> Self:
        if self.xmax < self.xmin or self.ymax < self.ymin:
            raise ValueError("box maximums must not be less than minimums")
        for value in (self.xmin, self.ymin, self.xmax, self.ymax):
            if len(f"{value:.10f}".rstrip("0").partition(".")[2]) > 5:
                raise ValueError("box coordinates must have at most five decimal places")
        return self


class Grounding(StrictModel):
    page: int = Field(ge=1)
    range: TextRange
    box: Box


class DraftGrounding(StrictModel):
    range: TextRange
    box: Box


class DraftTableCell(StrictModel):
    type: Literal["table_cell"] = "table_cell"
    grounding: DraftGrounding
    atomic_grounding: list[DraftGrounding]
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    colspan: int = Field(ge=1)
    rowspan: int = Field(ge=1)


class DraftLeaf(StrictModel):
    type: LeafType
    grounding: DraftGrounding
    atomic_grounding: list[DraftGrounding]


class DraftTable(StrictModel):
    type: Literal["table"] = "table"
    grounding: DraftGrounding
    children: list[DraftTableCell]


DraftElement = DraftLeaf | DraftTable


InlineStyle = Literal["plain", "strong", "emphasis"]
BlockStyle = Literal["plain", "heading_1", "heading_2", "heading_3"]
SemanticLeafType = Literal["text", "logo", "marginalia", "attestation", "scan_code"]


class SemanticText(StrictModel):
    kind: Literal["text"] = "text"
    text: str = Field(min_length=1)
    style: InlineStyle = "plain"

    @model_validator(mode="after")
    def validate_single_line(self) -> Self:
        if "\n" in self.text or "\r" in self.text:
            raise ValueError("semantic text spans must not contain line breaks")
        return self


class SemanticCheckbox(StrictModel):
    kind: Literal["checkbox"] = "checkbox"
    checked: bool


SemanticInline = SemanticText | SemanticCheckbox


class SemanticLine(StrictModel):
    content: list[SemanticInline] = Field(min_length=1, max_length=MAX_INLINE_RUNS_PER_LINE)
    box: Box
    style: BlockStyle = "plain"
    source_kind: Literal["printed", "handwritten", "uncertain"] = "printed"


class SemanticCellLine(StrictModel):
    content: list[SemanticInline] = Field(min_length=1, max_length=MAX_INLINE_RUNS_PER_LINE)
    source_kind: Literal["printed", "handwritten", "uncertain"] = "printed"


class SemanticLeaf(StrictModel):
    type: SemanticLeafType
    lines: list[SemanticLine] = Field(min_length=1, max_length=MAX_LINES_PER_ELEMENT)
    box: Box


class SemanticFigure(StrictModel):
    type: Literal["figure"] = "figure"
    figure_type: str = Field(min_length=1)
    description: SemanticLine
    lines: list[SemanticLine] = Field(default_factory=list, max_length=MAX_LINES_PER_ELEMENT)
    box: Box


class SemanticTableCell(StrictModel):
    type: Literal["table_cell"] = "table_cell"
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    colspan: int = Field(default=1, ge=1)
    rowspan: int = Field(default=1, ge=1)
    lines: list[SemanticCellLine] = Field(default_factory=list, max_length=MAX_LINES_PER_ELEMENT)
    render_in_markdown: bool = Field(
        default=True,
        description=(
            "Whether this structural cell has a corresponding HTML td in the canonical "
            "Markdown; false preserves geometry without emitting a td."
        ),
    )
    box: Box


class SemanticTable(StrictModel):
    type: Literal["table"] = "table"
    children: list[SemanticTableCell] = Field(min_length=1, max_length=MAX_TABLE_CELLS)
    box: Box


SemanticElement = SemanticLeaf | SemanticFigure | SemanticTable


class SemanticPageExtraction(StrictModel):
    """Model-facing page structure without presentation or character offsets."""

    children: list[SemanticElement] = Field(max_length=MAX_PAGE_ELEMENTS)


class PageExtraction(StrictModel):
    """One deterministically rendered page using page-local character offsets."""

    markdown: str
    children: list[DraftElement]


FindingCode = Literal[
    "suspected_omission",
    "suspected_mistranscription",
    "uncertain_reading_order",
    "uncertain_table_structure",
    "uncertain_grounding",
]


class AuditFinding(StrictModel):
    code: FindingCode
    severity: Literal["low", "medium", "high"]


class SegmentAudit(StrictModel):
    segment_index: int = Field(ge=0)
    completeness: Literal["complete", "uncertain", "missing"]
    image_agreement: Literal["supported", "uncertain", "contradicted"]
    findings: list[AuditFinding] = Field(max_length=MAX_AUDIT_FINDINGS_PER_SEGMENT)


class AuditedPageExtraction(PageExtraction):
    audits: list[SegmentAudit]

    @model_validator(mode="after")
    def validate_audits(self) -> Self:
        if [audit.segment_index for audit in self.audits] != list(range(len(self.children))):
            raise ValueError("audits must cover every segment once in reading order")
        return self


class AuditedSemanticPageExtraction(SemanticPageExtraction):
    audits: list[SegmentAudit]

    @model_validator(mode="after")
    def validate_audits(self) -> Self:
        if [audit.segment_index for audit in self.audits] != list(range(len(self.children))):
            raise ValueError("audits must cover every segment once in reading order")
        return self


class SemanticSegmentPatch(StrictModel):
    segment_id: str
    element: SemanticElement
    audit: SegmentAudit

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        if self.audit.segment_index != 0:
            raise ValueError("patch audit index must be zero")
        return self


class SemanticSegmentPatchBatch(StrictModel):
    patches: list[SemanticSegmentPatch] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_ids(self) -> Self:
        ids = [patch.segment_id for patch in self.patches]
        if len(ids) != len(set(ids)):
            raise ValueError("batch patch segment IDs must be unique")
        return self


class SemanticLineValue(StrictModel):
    content: list[SemanticInline] = Field(min_length=1, max_length=MAX_INLINE_RUNS_PER_LINE)
    style: BlockStyle = "plain"
    source_kind: Literal["printed", "handwritten", "uncertain"] = "printed"


class SemanticCellValue(StrictModel):
    lines: list[SemanticCellLine] = Field(default_factory=list, max_length=MAX_LINES_PER_ELEMENT)


class FieldResolution(StrictModel):
    field_id: str = Field(min_length=1)
    kind: Literal["line", "cell"]
    status: Literal["resolved", "needs_review"]
    line: SemanticLineValue | None = None
    cell: SemanticCellValue | None = None

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        populated = int(self.line is not None) + int(self.cell is not None)
        if self.status == "resolved" and populated != 1:
            raise ValueError("resolved fields require exactly one value")
        if self.status == "needs_review" and populated:
            raise ValueError("review fields must not include a value")
        if self.line is not None and self.kind != "line":
            raise ValueError("line value requires line kind")
        if self.cell is not None and self.kind != "cell":
            raise ValueError("cell value requires cell kind")
        return self


class SemanticFieldResolutionBatch(StrictModel):
    segment_id: str
    resolutions: list[FieldResolution] = Field(max_length=MAX_FIELD_RESOLUTIONS_PER_SEGMENT)


class PageStructure(StrictModel):
    children: list[DraftElement]


class LeafElement(StrictModel):
    type: LeafType
    id: str
    grounding: Grounding
    atomic_grounding: list[Grounding]


class TableCellElement(StrictModel):
    type: Literal["table_cell"] = "table_cell"
    id: str
    grounding: Grounding
    atomic_grounding: list[Grounding]
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    colspan: int = Field(ge=1)
    rowspan: int = Field(ge=1)


class TableElement(StrictModel):
    type: Literal["table"] = "table"
    id: str
    grounding: Grounding
    children: list[TableCellElement]


Element = Annotated[LeafElement | TableElement, Field(discriminator="type")]


class PageNode(StrictModel):
    type: Literal["page"] = "page"
    grounding: Grounding
    status: Literal["ok", "failed"] = "ok"
    reason: str | None = None
    children: list[Element]

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == "ok" and self.reason is not None:
            raise ValueError("successful pages cannot have a failure reason")
        if self.status == "failed":
            if not self.reason:
                raise ValueError("failed pages require a reason")
            if self.children:
                raise ValueError("failed pages cannot contain elements")
            if self.grounding.range.start != self.grounding.range.end:
                raise ValueError("failed pages require a zero-length range")
        if self.grounding.box != Box(xmin=0.0, ymin=0.0, xmax=1.0, ymax=1.0):
            raise ValueError("page grounding box must cover the full page")
        return self


class DocumentNode(StrictModel):
    type: Literal["document"] = "document"
    children: list[PageNode]


class Billing(StrictModel):
    service_tier: Literal["standard", "priority"]
    total_credits: float = Field(ge=0)


class ParseMetadata(StrictModel):
    job_id: str
    model_version: str
    page_count: int = Field(ge=1)
    output_markdown_chars: int = Field(ge=0)
    range_units: Literal["unicode_codepoints"] = "unicode_codepoints"
    openapi_spec: str
    failed_pages: list[int]
    duration_ms: int = Field(ge=0)
    billing: Billing

    @model_validator(mode="after")
    def validate_metadata(self) -> Self:
        if not JOB_ID_PATTERN.fullmatch(self.job_id):
            raise ValueError("job_id must use the observed parse ULID format")
        if self.failed_pages != sorted(set(self.failed_pages)):
            raise ValueError("failed_pages must be sorted and unique")
        if any(page < 1 or page > self.page_count for page in self.failed_pages):
            raise ValueError("failed page is outside the source page count")
        return self


class GroundTruthDocument(StrictModel):
    markdown: str
    metadata: ParseMetadata
    structure: DocumentNode

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        if len(self.markdown) != self.metadata.output_markdown_chars:
            raise ValueError("output_markdown_chars does not match markdown length")

        pages = self.structure.children
        page_numbers = [page.grounding.page for page in pages]
        if page_numbers != sorted(set(page_numbers)):
            raise ValueError("returned pages must be unique and in source order")
        if any(page > self.metadata.page_count for page in page_numbers):
            raise ValueError("returned page is outside the source page count")

        failed = [page.grounding.page for page in pages if page.status == "failed"]
        if failed != self.metadata.failed_pages:
            raise ValueError("failed_pages does not match page status")

        counters: Counter[str] = Counter()
        seen_ids: set[str] = set()
        markdown_length = len(self.markdown)
        for page in pages:
            page_number = page.grounding.page
            _check_range(page.grounding.range, markdown_length, "page")
            previous_start = -1
            for element in page.children:
                _check_element(
                    element,
                    page_number,
                    page.grounding.range,
                    markdown_length,
                    counters,
                    seen_ids,
                )
                if element.grounding.range.start < previous_start:
                    raise ValueError("page elements must be in reading order")
                previous_start = element.grounding.range.start
        return self


def _check_range(value: TextRange, limit: int, label: str) -> None:
    if value.end > limit:
        raise ValueError(f"{label} range exceeds markdown length")


def _check_contained(value: TextRange, parent: TextRange, label: str) -> None:
    if value.start < parent.start or value.end > parent.end:
        raise ValueError(f"{label} range must be inside its parent")


def _check_id(
    element_type: ElementType, element_id: str, counters: Counter[str], seen_ids: set[str]
) -> None:
    match = ID_PATTERN.fullmatch(element_id)
    if match is None or match.group(1) != element_type:
        raise ValueError(f"invalid id for {element_type}")
    expected = f"{element_type}-{counters[element_type]}"
    if element_id != expected:
        raise ValueError(f"expected id {expected}, received {element_id}")
    if element_id in seen_ids:
        raise ValueError("element ids must be unique within a document")
    counters[element_type] += 1
    seen_ids.add(element_id)


def _check_element(
    element: Element,
    page_number: int,
    page_range: TextRange,
    markdown_length: int,
    counters: Counter[str],
    seen_ids: set[str],
) -> None:
    _check_id(element.type, element.id, counters, seen_ids)
    if element.grounding.page != page_number:
        raise ValueError("element page must match its containing page")
    _check_range(element.grounding.range, markdown_length, "element")
    _check_contained(element.grounding.range, page_range, "element")

    if isinstance(element, TableElement):
        for cell in element.children:
            _check_id(cell.type, cell.id, counters, seen_ids)
            if cell.grounding.page != page_number:
                raise ValueError("table cell page must match its containing page")
            _check_contained(cell.grounding.range, element.grounding.range, "table cell")
            for atomic in cell.atomic_grounding:
                if atomic.page != page_number:
                    raise ValueError("atomic grounding page must match its table cell")
                _check_contained(atomic.range, cell.grounding.range, "atomic grounding")
    else:
        for atomic in element.atomic_grounding:
            if atomic.page != page_number:
                raise ValueError("atomic grounding page must match its element")
            _check_contained(atomic.range, element.grounding.range, "atomic grounding")
