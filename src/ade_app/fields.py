"""Deterministic field discovery, cross-page linking, and business checks.

Responsible for extracting candidate key-value pairs from Markdown/HTML tables,
mapping canonical aliases, linking occurrences across pages, validating evidence coordinates,
and generating `ExtractedFieldV3` records.
Must NOT call models or alter source text.
Next: ade_app.services.validation or ade_app.pipeline for schema validation.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from ade_app.models import (
    Box,
    DraftTable,
    ExtractedField,
    ExtractedFieldV3,
    ExtractionDocumentV2,
    FieldEvidence,
    FieldValidationCheck,
    GroundTruthDocument,
    PageExtraction,
    ValueState,
)
from ade_app.raster import RenderedPage, crop_segment
from ade_app.rendering import PageOutcome

_FIELD = re.compile(
    r"(?m)^[ \t]*(?:[-*][ \t]*)?(?P<n>[A-Za-z][A-Za-z0-9 /_.#()-]{0,60}?)"
    r"[ \t]*:[ \t]*(?P<v>[^\n|]{0,200})[ \t]*$"
)
_CHECKBOX = re.compile(r"(?m)^\s*(?:[-*]\s*)?\[(?P<s>[xX ?])\]\s*(?P<n>[^\n]{1,80}?)\s*$")
_TABLE_ROW = re.compile(r"(?m)^\s*\|(?P<row>[^\n]+)\|\s*$")
_HTML_ROW = re.compile(
    r"(?is)<tr[^>]*>\s*<t[dh][^>]*>(?P<name>.*?)</t[dh]>\s*"
    r"<t[dh][^>]*>(?P<value>.*?)</t[dh]>\s*</tr>"
)
_NON_WORD = re.compile(r"[^a-z0-9]+")
_ALIASES = {
    "member id": "member_id",
    "member number": "member_id",
    "subscriber id": "member_id",
    "npi": "npi",
    "national provider identifier": "npi",
    "tin": "tin",
    "tax id": "tin",
    "tax identification number": "tin",
    "ein": "tin",
    "date of birth": "dob",
    "birth date": "dob",
    "dob": "dob",
    "address": "address",
    "member address": "member_address",
    "patient address": "member_address",
    "provider address": "provider_address",
    "practice address": "provider_address",
}


@dataclass(frozen=True, slots=True)
class RawFieldCandidate:
    """Unlinked field occurrence emitted by semantic extraction."""

    original_name: str
    canonical_name: str
    value: str | bool
    evidence: FieldEvidence
    reasons: tuple[str, ...]
    order: int


def build_v2_artifact(
    document: GroundTruthDocument,
    page_records: Iterable[Any] = (),
    *,
    review_threshold: float = 75.0,
) -> ExtractionDocumentV2:
    raw = _document_candidates(document, page_records)
    return ExtractionDocumentV2(
        markdown=document.markdown,
        metadata=document.metadata,
        structure=document.structure,
        fields=link_and_validate_fields(raw, review_threshold=review_threshold),
    )


def discover_raw_fields(
    outcomes: Iterable[PageOutcome],
    page_records: Iterable[Any] = (),
    pages: Iterable[RenderedPage] = (),
) -> tuple[RawFieldCandidate, ...]:
    """Discover page-local values without linking or applying business rules."""

    records = {record.source_page: record for record in page_records}
    images = {page.source_page: page for page in pages}
    counters: Counter[str] = Counter()
    result: list[RawFieldCandidate] = []
    order = 0
    for outcome in outcomes:
        extraction = outcome.candidate_extraction or outcome.extraction
        if extraction is None:
            continue
        element_ids = []
        for element in extraction.children:
            element_ids.append(f"{element.type}-{counters[element.type]}")
            counters[element.type] += 1
        for offset, name, value in sorted(_found_fields(extraction.markdown)):
            try:
                element_index, box = _page_grounding_for_offset(extraction, offset)
            except ValueError as error:
                raise ValueError("field candidate lacks source grounding") from error
            semantic_id = _semantic_id(extraction, element_index, offset)
            route, confidence, reasons = _segment_evidence(
                records.get(outcome.source_page), element_index, semantic_id
            )
            result.append(
                RawFieldCandidate(
                    original_name=name,
                    canonical_name=_canonical_name(name),
                    value=value,
                    evidence=FieldEvidence(
                        page=outcome.source_page,
                        region_id=element_ids[element_index],
                        box=box,
                        route=route,
                        candidate=value,
                        confidence=confidence,
                        semantic_id=semantic_id,
                        crop_sha256=hashlib.sha256(
                            crop_segment(images[outcome.source_page], box).png_bytes
                        ).hexdigest()
                        if outcome.source_page in images
                        else None,
                        verification="agreed"
                        if "visual_confirmation_agreed" in reasons
                        else "calibrated"
                        if "calibrated_acceptance" in reasons
                        else "unresolved",
                    ),
                    reasons=reasons,
                    order=order,
                )
            )
            order += 1
    return tuple(result)


def link_and_validate_fields(
    raw_fields: Iterable[RawFieldCandidate], *, review_threshold: float = 75.0
) -> list[ExtractedField]:
    """Link occurrences, run business checks, and finalize field confidence."""

    candidates: dict[str, list[RawFieldCandidate]] = defaultdict(list)
    for candidate in raw_fields:
        candidates[candidate.canonical_name].append(candidate)

    fields: list[ExtractedField] = []
    for index, (canonical, values) in enumerate(candidates.items()):
        ranked = sorted(
            values,
            key=lambda item: (-item.evidence.confidence, item.evidence.page, item.order),
        )
        selected = ranked[0].value
        conflict = len({_normalized(item.value) for item in values}) > 1
        checks = _checks(canonical, selected)
        reasons = list(dict.fromkeys(reason for item in values for reason in item.reasons))
        confidence, status = ranked[0].evidence.confidence, "accepted"
        original_name = ranked[0].original_name
        if conflict:
            status, confidence = "conflict", min(confidence, 49.0)
            reasons.append(f"{canonical}_differs_across_pages")
            checks.insert(
                0,
                FieldValidationCheck(
                    rule=f"{canonical}_consistency",
                    passed=False,
                    message=f"{original_name} values differ across pages.",
                ),
            )
        elif len(values) > 1:
            checks.insert(
                0,
                FieldValidationCheck(
                    rule=f"{canonical}_consistency",
                    passed=True,
                    message=f"{original_name} is consistent across observed pages.",
                ),
            )
        failed = [check for check in checks if not check.passed]
        if failed:
            status = status if status == "conflict" else "needs_review"
            confidence = min(confidence, 49.0)
            reasons.extend(f"failed_{check.rule}" for check in failed)
        elif "visual_confirmation_agreed" not in reasons and "calibrated_acceptance" not in reasons:
            status = "needs_review"
            reasons.append("unverified_evidence")
        if isinstance(selected, str) and (
            not selected.strip()
            or any(marker in selected for marker in ("[ILLEGIBLE_TEXT]", "[UNVERIFIED]", "[?]"))
        ):
            status = "needs_review"
            reasons.append("unresolved_value")
        fields.append(
            ExtractedField(
                field_id=f"field-{index}",
                original_name=original_name,
                canonical_name=canonical,
                value=selected,
                confidence=confidence,
                status=status,
                reasons=list(dict.fromkeys(reasons)),
                evidence=[item.evidence for item in ranked],
                validation_checks=checks,
            )
        )
    return fields


def _document_candidates(
    document: GroundTruthDocument, page_records: Iterable[Any]
) -> tuple[RawFieldCandidate, ...]:
    records = {record.source_page: record for record in page_records}
    result = []
    for order, (offset, name, value) in enumerate(sorted(_found_fields(document.markdown))):
        page, region_id, box, element_index = _grounding_for_offset(document, offset)
        route, confidence, reasons = _segment_evidence(records.get(page), element_index)
        evidence = FieldEvidence(
            page=page,
            region_id=region_id,
            box=box,
            route=route,
            candidate=value,
            confidence=confidence,
        )
        result.append(
            RawFieldCandidate(name, _canonical_name(name), value, evidence, reasons, order)
        )
    return tuple(result)


def _found_fields(markdown: str) -> list[tuple[int, str, str | bool]]:
    found: list[tuple[int, str, str | bool]] = []
    for match in _FIELD.finditer(markdown):
        raw = match.group("v").strip()
        checkbox = re.fullmatch(r"\[([xX ?])\]", raw)
        found.append(
            (
                match.start("n"),
                match.group("n").strip(),
                ("[?]" if checkbox.group(1) == "?" else checkbox.group(1).lower() == "x")
                if checkbox
                else raw,
            )
        )
    found.extend(
        (
            match.start("n"),
            match.group("n").strip(),
            "[?]" if match.group("s") == "?" else match.group("s").lower() == "x",
        )
        for match in _CHECKBOX.finditer(markdown)
    )
    found.extend(_table_fields(markdown))
    found.extend(
        (
            match.start("value"),
            re.sub(r"<[^>]+>", "", match.group("name")).strip(),
            re.sub(r"<[^>]+>", "", match.group("value")).strip(),
        )
        for match in _HTML_ROW.finditer(markdown)
    )
    return found


def _page_grounding_for_offset(extraction: PageExtraction, offset: int) -> tuple[int, Box]:
    for index, element in enumerate(extraction.children):
        if element.grounding.range.start <= offset < element.grounding.range.end:
            if isinstance(element, DraftTable):
                for cell in element.children:
                    if cell.grounding.range.start <= offset < cell.grounding.range.end:
                        return index, cell.grounding.box
            for atom in getattr(element, "atomic_grounding", ()):
                if atom.range.start <= offset < atom.range.end:
                    return index, atom.box
            return index, element.grounding.box
    raise ValueError("field offset has no source region")


def apply_postprocessing_resolutions(
    artifact: ExtractionDocumentV2,
    resolutions: dict[str, tuple[str | bool, float]],
    *,
    review_threshold: float = 75.0,
) -> ExtractionDocumentV2:
    """Apply bounded repair decisions, then re-run deterministic validation."""

    fields = apply_field_resolutions(
        artifact.fields, resolutions, review_threshold=review_threshold
    )
    return ExtractionDocumentV2.model_validate(
        artifact.model_copy(update={"fields": fields}).model_dump()
    )


def apply_field_resolutions(
    source_fields: Iterable[ExtractedField],
    resolutions: dict[str, tuple[str | bool, float]],
    *,
    review_threshold: float = 75.0,
) -> list[ExtractedField]:
    """Apply repair values and re-run validation without assembling an artifact."""

    fields = []
    for field in source_fields:
        resolution = resolutions.get(field.field_id)
        if resolution is None:
            fields.append(field)
            continue
        value, model_confidence = resolution
        if field.status == "conflict":
            fields.append(
                field.model_copy(
                    update={
                        "reasons": [
                            *field.reasons,
                            "repair_confirmation_cannot_resolve_conflict",
                        ]
                    }
                )
            )
            continue
        if _normalized(value) != _normalized(field.value):
            fields.append(
                field.model_copy(
                    update={
                        "status": "needs_review",
                        "confidence": min(field.confidence, 49.0),
                        "reasons": [*field.reasons, "repair_proposed_new_value_ignored"],
                    }
                )
            )
            continue
        checks = _checks(field.canonical_name, value)
        if any(not check.passed for check in checks):
            fields.append(
                field.model_copy(
                    update={"reasons": [*field.reasons, "repair_repair_failed_validation"]}
                )
            )
            continue
        source = field.evidence[0]
        evidence = FieldEvidence(
            page=source.page,
            region_id=source.region_id,
            box=source.box,
            route="repair",
            candidate=value,
            confidence=min(model_confidence, 90.0),
        )
        resolved_confidence = min(model_confidence, 90.0)
        fields.append(
            field.model_copy(
                update={
                    "value": value,
                    "confidence": resolved_confidence,
                    "status": (
                        "accepted" if resolved_confidence >= review_threshold else "needs_review"
                    ),
                    "reasons": [*field.reasons, "field_repaired"],
                    "evidence": [*field.evidence, evidence],
                    "validation_checks": checks,
                }
            )
        )
    return fields


def _table_fields(markdown: str) -> list[tuple[int, str, str]]:
    result = []
    for match in _TABLE_ROW.finditer(markdown):
        cells = [cell.strip() for cell in match.group("row").split("|")]
        if (
            len(cells) == 2
            and cells[0]
            and cells[1]
            and _canonical_name(cells[0]) in _ALIASES.values()
            and not all(set(cell) <= {"-", ":", " "} for cell in cells)
        ):
            result.append(
                (match.start("row") + match.group("row").find("|") + 1, cells[0], cells[1])
            )
    return result


def _checks(name: str, value: str | bool) -> list[FieldValidationCheck]:
    if not isinstance(value, str):
        return []
    if name == "npi":
        valid = _valid_npi(value)
        message = (
            "NPI checksum is valid."
            if valid
            else "NPI must contain 10 digits with a valid checksum."
        )
        return [FieldValidationCheck(rule="npi_checksum", passed=valid, message=message)]
    if name == "tin":
        valid = (
            re.fullmatch(r"[\d\s-]+", value.strip()) is not None
            and len(re.sub(r"\D", "", value)) == 9
        )
        message = "TIN contains nine digits." if valid else "TIN must contain nine digits."
        return [FieldValidationCheck(rule="tin_format", passed=valid, message=message)]
    if name == "member_id":
        valid = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,39}", value.strip()) is not None
        return [
            FieldValidationCheck(
                rule="member_id_format",
                passed=valid,
                message="Member ID format is valid."
                if valid
                else "Member ID must be 3-40 permitted characters.",
            )
        ]
    if name == "dob":
        parsed = _parse_date(value)
        valid = (
            parsed is not None
            and parsed <= date.today()
            and (date.today() - parsed).days <= 130 * 366
        )
        return [
            FieldValidationCheck(
                rule="dob_plausibility",
                passed=valid,
                message="DOB is plausible."
                if valid
                else "DOB is invalid, future-dated, or implies age over 130.",
            )
        ]
    if name == "address" or name.endswith("_address"):
        valid = bool(value.strip())
        return [
            FieldValidationCheck(
                rule="address_nonblank",
                passed=valid,
                message="Address is nonblank." if valid else "Address is blank.",
            )
        ]
    return []


def _parse_date(value: str) -> date | None:
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            pass
    return None


def _normalized(value: str | bool) -> str | bool:
    return value if isinstance(value, bool) else re.sub(r"\s+", " ", value).strip().casefold()


def _canonical_name(name: str) -> str:
    normalized = _NON_WORD.sub(" ", name.lower()).strip()
    return _ALIASES.get(normalized, normalized.replace(" ", "_"))


def _grounding_for_offset(document: GroundTruthDocument, offset: int) -> tuple[int, str, Box, int]:
    for page in document.structure.children:
        if page.grounding.range.start <= offset < page.grounding.range.end:
            for index, element in enumerate(page.children):
                if element.grounding.range.start <= offset < element.grounding.range.end:
                    return page.grounding.page, element.id, element.grounding.box, index
    raise ValueError("field offset has no source region")


def _segment_evidence(
    record: Any | None,
    index: int,
    semantic_id: str | None = None,
) -> tuple[
    Literal["local_text", "local_table", "primary", "verification", "repair"],
    float,
    tuple[str, ...],
]:
    if record is None or index >= len(record.segments):
        return "verification", 0.0, ("missing_evidence",)
    segment = record.segments[index]
    unresolved = getattr(segment, "unresolved_fields", ())
    status = getattr(segment, "status", "needs_review")
    verified = status in {"accepted_consensus", "accepted_resolution"}
    if unresolved and semantic_id is not None and semantic_id not in unresolved:
        verified = not getattr(segment, "structural_conflicts", ())
    attempted = any(attempt.stage != "primary" for attempt in getattr(segment, "attempts", ()))
    reasons = (*segment.reasons, *(("verification_attempted",) if attempted else ()))
    if verified:
        reasons = ("visual_confirmation_agreed", "verification_attempted")
    elif status == "accepted_quality" and "calibrated_acceptance" in reasons:
        reasons = ("calibrated_acceptance", "verification_attempted")
    else:
        reasons = (*reasons, "unverified_evidence")
    return segment.final_route, segment.final_score, reasons


def _semantic_id(extraction: PageExtraction, index: int, offset: int) -> str:
    element = extraction.children[index]
    if isinstance(element, DraftTable):
        for cell in element.children:
            if cell.grounding.range.start <= offset < cell.grounding.range.end:
                return f"cell-{cell.row}-{cell.col}"
    for line, atom in enumerate(getattr(element, "atomic_grounding", ())):
        if atom.range.start <= offset < atom.range.end:
            return f"line-{line}"
    return "region"


def public_fields(fields: Iterable[ExtractedField]) -> list[ExtractedFieldV3]:
    """Publish candidates as evidence; never as unsupported selected values."""
    result = []
    for field in fields:
        verified = any(e.verification in {"agreed", "calibrated"} for e in field.evidence)
        state: ValueState = "observed"
        status = field.status
        if status == "conflict":
            state = "conflicting"
        elif isinstance(field.value, str) and "[ILLEGIBLE_TEXT]" in field.value:
            state = "illegible"
        elif isinstance(field.value, str) and "[?]" in field.value:
            state = "ambiguous"
        elif isinstance(field.value, str) and not field.value.strip():
            state = "blank"
        elif status != "accepted" or not verified or field.value == "[UNVERIFIED]":
            state = "unverified"
        if not verified or state not in {"observed", "blank"}:
            status = "conflict" if state == "conflicting" else "needs_review"
        probabilities = [e.calibrated_probability for e in field.evidence]
        confidence = (
            min(p for p in probabilities if p is not None)
            if probabilities and all(p is not None for p in probabilities)
            else None
        )
        result.append(
            ExtractedFieldV3(
                field_id=field.field_id,
                original_name=field.original_name,
                canonical_name=field.canonical_name,
                value=field.value if state == "observed" else None,
                value_state=state,
                confidence=confidence,
                routing_score=field.confidence,
                status=status,
                reasons=field.reasons,
                evidence=field.evidence,
                validation_checks=field.validation_checks,
            )
        )
    return result


def _valid_npi(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 10:
        return False
    total = 24  # Luhn contribution of the required health-care prefix ``80840``.
    for index, character in enumerate(digits[:-1]):
        digit = int(character) * (2 if index % 2 == 0 else 1)
        total += digit // 10 + digit % 10
    return (10 - total % 10) % 10 == int(digits[-1])
