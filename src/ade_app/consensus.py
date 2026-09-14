"""Deterministic field comparison and document-local peer evidence.

Responsible for diffing primary and independent verification extractions,
detecting field disagreements (line and table cell values), and compiling peer
evidence for consensus gates.
Must NOT invoke models or execute network calls.
Next: ade_app.openai_client for Sol dispute resolution, or ade_app.fields for field linking.
"""

from __future__ import annotations

import io
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter, ImageStat

from ade_app.models import (
    Box,
    FieldResolution,
    SemanticCellLine,
    SemanticCheckbox,
    SemanticElement,
    SemanticFigure,
    SemanticInline,
    SemanticLine,
    SemanticTable,
    SemanticText,
)
from ade_app.raster import RenderedCrop, RenderedPage, crop_segment

_VARIABLE_TEXT = re.compile(
    r"(?:\b(?:page|p)\s*\d+\s*(?:/|of)\s*\d+\b|"
    r"\b(?:fax|sent|received|date|time)\b|"
    r"\b\d{1,2}[:/]\d{1,2}(?::\d{2})?(?:[/]\d{2,4})?\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SemanticField:
    field_id: str
    kind: Literal["line", "cell"]
    value: SemanticLine | list[SemanticCellLine]
    box: Box


@dataclass(frozen=True, slots=True)
class FieldDisagreement:
    field_id: str
    kind: Literal["line", "cell"]
    primary: SemanticField
    independent: SemanticField


@dataclass(frozen=True, slots=True)
class Comparison:
    agreeing_fields: tuple[str, ...]
    disagreements: tuple[FieldDisagreement, ...]
    structural_conflicts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PeerEvidence:
    png_bytes: bytes
    source_page: int
    similarity: float
    target_clarity: float
    peer_clarity: float


@dataclass(frozen=True, slots=True)
class PeerSegment:
    segment_id: str
    page: RenderedPage
    element: SemanticElement


def compare_elements(primary: SemanticElement, independent: SemanticElement) -> Comparison:
    """Compare aligned semantic fields while deliberately ignoring coordinates."""

    if type(primary) is not type(independent) or primary.type != independent.type:
        return Comparison((), (), ("segment_type",))
    primary_fields, primary_structure = _fields(primary)
    independent_fields, independent_structure = _fields(independent)
    conflicts = [] if primary_structure == independent_structure else ["segment_topology"]
    if primary_fields.keys() != independent_fields.keys():
        conflicts.append("field_set")
    agreeing: list[str] = []
    disagreements: list[FieldDisagreement] = []
    for field_id in sorted(primary_fields.keys() & independent_fields.keys()):
        left, right = primary_fields[field_id], independent_fields[field_id]
        if _field_key(left) == _field_key(right):
            agreeing.append(field_id)
        else:
            disagreements.append(FieldDisagreement(field_id, left.kind, left, right))
    return Comparison(tuple(agreeing), tuple(disagreements), tuple(conflicts))


def apply_resolutions(
    element: SemanticElement,
    disagreements: tuple[FieldDisagreement, ...],
    resolutions: list[FieldResolution],
) -> tuple[SemanticElement, tuple[str, ...]]:
    """Apply resolved values to the primary element while preserving its geometry/topology."""

    result = element.model_copy(deep=True)
    expected = {item.field_id: item for item in disagreements}
    resolution_by_id = {item.field_id: item for item in resolutions}
    unresolved: list[str] = []
    for field_id, disagreement in expected.items():
        resolution = resolution_by_id.get(field_id)
        if resolution is None or resolution.status != "resolved":
            unresolved.append(field_id)
            continue
        if resolution.kind != disagreement.kind:
            unresolved.append(field_id)
            continue
        candidate = result.model_copy(deep=True)
        if not _replace_field(candidate, field_id, resolution):
            unresolved.append(field_id)
            continue
        proposed, _ = _fields(candidate)
        if _field_key(proposed[field_id]) not in (
            _field_key(disagreement.primary),
            _field_key(disagreement.independent),
        ):
            unresolved.append(field_id)
            continue
        result = candidate
    unresolved.extend(sorted(resolution_by_id.keys() - expected.keys()))
    return result, tuple(unresolved)


def build_peer_evidence(segments: list[PeerSegment]) -> dict[str, PeerEvidence]:
    """Choose at most one clearer, stable, different-page peer per repeated segment."""

    evidence: dict[str, PeerEvidence] = {}
    descriptors = [(_signature(item.element), _topology(item.element), item) for item in segments]
    for signature, topology, target in descriptors:
        if not signature:
            continue
        target_crop = crop_segment(target.page, target.element.box)
        target_clarity = _clarity(target_crop.png_bytes)
        candidates: list[tuple[float, float, str, PeerSegment]] = []
        for peer_signature, peer_topology, peer in descriptors:
            if (
                peer.segment_id == target.segment_id
                or peer.page.source_page == target.page.source_page
            ):
                continue
            if topology != peer_topology or not _compatible_shape(
                target.element.box, peer.element.box
            ):
                continue
            similarity = SequenceMatcher(None, signature, peer_signature).ratio()
            if similarity < 0.85:
                continue
            peer_crop = crop_segment(peer.page, peer.element.box)
            peer_clarity = _clarity(peer_crop.png_bytes)
            if peer_clarity > target_clarity:
                candidates.append((peer_clarity, similarity, peer_signature, peer))
        if not candidates:
            continue
        candidate_signatures = [item[2] for item in candidates]
        if any(
            SequenceMatcher(None, left, right).ratio() < 0.85
            for index, left in enumerate(candidate_signatures)
            for right in candidate_signatures[index + 1 :]
        ):
            continue
        peer_clarity, similarity, _, peer = max(candidates, key=lambda item: (item[0], item[1]))
        peer_crop = crop_segment(peer.page, peer.element.box)
        evidence[target.segment_id] = PeerEvidence(
            _masked_crop(peer_crop, peer.element),
            peer.page.source_page,
            round(similarity, 4),
            round(target_clarity, 4),
            round(peer_clarity, 4),
        )
    return evidence


def _fields(element: SemanticElement) -> tuple[dict[str, SemanticField], tuple[object, ...]]:
    fields: dict[str, SemanticField] = {}
    if isinstance(element, SemanticTable):
        topology = tuple(
            (cell.row, cell.col, cell.rowspan, cell.colspan)
            for cell in sorted(element.children, key=lambda cell: (cell.row, cell.col))
        )
        for cell in element.children:
            field_id = f"cell-{cell.row}-{cell.col}"
            fields[field_id] = SemanticField(field_id, "cell", cell.lines, cell.box)
        return fields, topology
    if isinstance(element, SemanticFigure):
        fields["description"] = SemanticField(
            "description", "line", element.description, element.description.box
        )
    for index, line in enumerate(element.lines):
        field_id = f"line-{index}"
        fields[field_id] = SemanticField(field_id, "line", line, line.box)
    return fields, (element.type, len(fields))


def _field_key(field: SemanticField) -> object:
    if field.kind == "line":
        line = field.value
        if not isinstance(line, SemanticLine):
            raise TypeError("line field requires a semantic line")
        return (_content_key(line.content), line.style, line.source_kind)
    lines = field.value
    if not isinstance(lines, list):
        raise TypeError("cell field requires semantic cell lines")
    return tuple((_content_key(line.content), line.source_kind) for line in lines)


def _content_key(content: Sequence[SemanticInline]) -> tuple[object, ...]:
    values: list[object] = []
    for item in content:
        if isinstance(item, SemanticCheckbox):
            values.append(("checkbox", item.checked))
        elif isinstance(item, SemanticText):
            values.append(("text", _normalize(item.text), item.style))
    return tuple(values)


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _replace_field(element: SemanticElement, field_id: str, resolution: FieldResolution) -> bool:
    if isinstance(element, SemanticTable):
        for cell in element.children:
            if field_id == f"cell-{cell.row}-{cell.col}" and resolution.cell is not None:
                cell.lines = resolution.cell.lines
                return True
        return False
    if isinstance(element, SemanticFigure) and field_id == "description":
        if resolution.line is None:
            return False
        box = element.description.box
        element.description = SemanticLine(box=box, **resolution.line.model_dump())
        return True
    if not field_id.startswith("line-") or resolution.line is None:
        return False
    try:
        index = int(field_id.removeprefix("line-"))
        old = element.lines[index]
    except (ValueError, IndexError):
        return False
    element.lines[index] = SemanticLine(box=old.box, **resolution.line.model_dump())
    return True


def _signature(element: SemanticElement) -> str:
    values: list[str] = []
    fields, _ = _fields(element)
    for field in fields.values():
        lines = [field.value] if isinstance(field.value, SemanticLine) else field.value
        for line in lines:
            if line.source_kind != "printed" or any(
                getattr(item, "kind", None) == "checkbox" for item in line.content
            ):
                continue
            text = " ".join(getattr(item, "text", "") for item in line.content)
            if "[ILLEGIBLE_TEXT]" in text or _VARIABLE_TEXT.search(text):
                continue
            values.append(_normalize(text).casefold())
    return "\n".join(value for value in values if value)


def _topology(element: SemanticElement) -> tuple[object, ...]:
    return _fields(element)[1]


def _compatible_shape(left: Box, right: Box) -> bool:
    left_width = left.xmax - left.xmin
    right_width = right.xmax - right.xmin
    if left_width <= 0 or right_width <= 0:
        return False
    left_ratio = left_width / max(0.00001, left.ymax - left.ymin)
    right_ratio = right_width / max(0.00001, right.ymax - right.ymin)
    return 0.8 <= left_ratio / right_ratio <= 1.25


def _clarity(png_bytes: bytes) -> float:
    with Image.open(io.BytesIO(png_bytes)) as image:
        edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
        return float(ImageStat.Stat(edges).var[0])


def _masked_crop(crop: RenderedCrop, element: SemanticElement) -> bytes:
    with Image.open(io.BytesIO(crop.png_bytes)).convert("RGB") as image:
        draw = ImageDraw.Draw(image)
        fields, _ = _fields(element)
        for field in fields.values():
            lines = [field.value] if isinstance(field.value, SemanticLine) else field.value
            variable = any(
                line.source_kind != "printed"
                or any(getattr(item, "kind", None) == "checkbox" for item in line.content)
                or "[ILLEGIBLE_TEXT]"
                in " ".join(getattr(item, "text", "") for item in line.content)
                or _VARIABLE_TEXT.search(
                    " ".join(getattr(item, "text", "") for item in line.content)
                )
                for line in lines
            )
            if variable:
                draw.rectangle(_box_in_crop(field.box, crop), fill="white")
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()


def _box_in_crop(box: Box, crop: RenderedCrop) -> tuple[int, int, int, int]:
    left = round(box.xmin * crop.page_width - crop.left)
    top = round(box.ymin * crop.page_height - crop.top)
    right = round(box.xmax * crop.page_width - crop.left)
    bottom = round(box.ymax * crop.page_height - crop.top)
    return (
        max(0, min(crop.width, left)),
        max(0, min(crop.height, top)),
        max(0, min(crop.width, right)),
        max(0, min(crop.height, bottom)),
    )
