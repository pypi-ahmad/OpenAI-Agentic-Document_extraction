"""Deterministic segment signals and calibrated routing scores.

Turns a segment's audit/extraction into a fixed feature vector and scores it
against a calibrated logistic profile loaded from profiles/segment-quality-*.json.
This module only computes the score and reasons — it must not decide routing;
every segment with non-empty reasons (i.e. "uncalibrated") still requires
independent verification by the caller regardless of its raw score. Next:
openai_client.py, which turns this score into the luna/terra/sol routing
decision.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ade_app.constants import VERIFICATION_MODEL
from ade_app.models import DraftElement, DraftTable, PageExtraction, SegmentAudit
from ade_app.verification import verify_sensitive_values

# Positional contract: segment_features() must emit values in this exact
# order — QualityProfile.score() zips them against coefficients by position,
# and validate_shape() only guards that a loaded profile's declared
# feature_names equal this tuple, not that anyone emits values in this order.
FEATURE_NAMES = (
    "schema_validity",
    "text_completeness",
    "valid_bounding_boxes",
    "ocr_cleanliness",
    "table_consistency",
    "image_agreement",
    "verifier_findings",
)


class QualityProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    profile_version: int = 1
    feature_names: tuple[str, ...]
    coefficients: tuple[float, ...]
    intercept: float
    threshold: float = Field(ge=0, le=100)
    prompt_hashes: dict[str, str]
    groundtruth_hashes: dict[str, str]
    validation: dict[str, float | int]
    raw_decision_boundary: float = Field(default=90.0, ge=0, le=100)
    routing_fingerprint: str = ""
    model_id: str = ""
    reasoning_effort: str = ""

    @property
    def routing_mode(self) -> str:
        return (
            "repair_all" if int(self.validation.get("accepted_count", 0)) == 0 else "quality_gated"
        )

    @model_validator(mode="after")
    def validate_shape(self) -> QualityProfile:
        if self.feature_names != FEATURE_NAMES:
            raise ValueError("quality profile feature set is incompatible")
        if len(self.coefficients) != len(FEATURE_NAMES):
            raise ValueError("quality profile coefficient count is incompatible")
        return self

    def score(self, features: tuple[float, ...]) -> float:
        if len(features) != len(self.coefficients):
            raise ValueError("quality feature count is incompatible")
        products = zip(self.coefficients, features, strict=True)
        value = self.intercept + sum(coefficient * feature for coefficient, feature in products)
        # Numerically stable logistic: avoids exp() overflow for large |value|.
        if value >= 0:
            probability = 1 / (1 + math.exp(-value))
        else:
            exp_value = math.exp(value)
            probability = exp_value / (1 + exp_value)
        raw = probability * 100
        if self.profile_version < 2:
            return round(raw, 2)
        # v2+ remaps the raw probability so raw_decision_boundary (the
        # cross-validated accept/reject cutoff) lands exactly on `threshold`
        # in output space, without changing relative order, so a caller can
        # compare the mapped score against one fixed threshold regardless of
        # where calibration actually placed the boundary.
        boundary = self.raw_decision_boundary
        if boundary >= 100:
            mapped = raw * 0.9
        elif raw < boundary:
            mapped = raw / max(boundary, 0.000001) * self.threshold
        else:
            mapped = self.threshold + (raw - boundary) / (100 - boundary) * (100 - self.threshold)
        return round(min(100.0, mapped), 2)


def load_quality_profile(
    path: str | Path,
    *,
    expected_model: tuple[str, str] = VERIFICATION_MODEL,
) -> QualityProfile:
    """Load a calibration file, failing closed if it is missing, stale, or for the wrong model."""

    profile_path = Path(path)
    if not profile_path.is_file():
        raise RuntimeError(f"Calibrated quality profile is unavailable: {profile_path.resolve()}")
    profile = QualityProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))
    if profile.profile_version not in {3, 4}:
        raise RuntimeError("Quality profile v3 calibration is required for Terra routing")
    if (profile.model_id, profile.reasoning_effort) != expected_model:
        raise RuntimeError("Quality profile does not match the active verification model")
    return profile


@dataclass(frozen=True, slots=True)
class SegmentQuality:
    features: tuple[float, ...]
    score: float
    reasons: tuple[str, ...]


def measure_segment(
    extraction: PageExtraction,
    index: int,
    audit: SegmentAudit,
    profile: QualityProfile,
) -> SegmentQuality:
    features, reasons = segment_features(extraction, index, audit)
    return SegmentQuality(features, profile.score(features), reasons)


def segment_features(
    extraction: PageExtraction,
    index: int,
    audit: SegmentAudit,
    *,
    include_business_rules: bool = False,
) -> tuple[tuple[float, ...], tuple[str, ...]]:
    element = extraction.children[index]
    text = extraction.markdown[element.grounding.range.start : element.grounding.range.end]
    completeness = {"complete": 1.0, "uncertain": 0.5, "missing": 0.0}[audit.completeness]
    agreement = {"supported": 1.0, "uncertain": 0.5, "contradicted": 0.0}[audit.image_agreement]
    boxes = _box_score(element)
    cleanliness = _ocr_cleanliness(text)
    table = _table_consistency(element, text)
    verifier_findings = verify_sensitive_values(text) if include_business_rules else []
    finding_score = max(0.0, _finding_score(audit) - 0.25 * len(verifier_findings))
    features = (1.0, completeness, boxes, cleanliness, table, agreement, finding_score)
    reasons = []
    if completeness < 1:
        reasons.append(f"completeness:{audit.completeness}")
    if boxes < 1:
        reasons.append("invalid_or_empty_box")
    if cleanliness < 1:
        reasons.append("ocr_anomaly")
    if table < 1:
        reasons.append("table_inconsistency")
    if agreement < 1:
        reasons.append(f"image_agreement:{audit.image_agreement}")
    reasons.extend(f"{finding.severity}:{finding.code}" for finding in audit.findings)
    reasons.extend(f"verifier:{finding.code}" for finding in verifier_findings)
    return features, tuple(reasons)


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    document: str
    features: tuple[float, ...]
    actual_quality: float
    human_verified: bool = False


def document_family(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    if "amerigroup" in normalized:
        return "amerigroup"
    return re.sub(r"[0-9]+$", "", normalized)


def fit_quality_profile(
    samples: list[CalibrationSample],
    *,
    prompt_hashes: dict[str, str],
    groundtruth_hashes: dict[str, str],
    threshold: float = 90.0,
    model_id: str = VERIFICATION_MODEL[0],
    reasoning_effort: str = VERIFICATION_MODEL[1],
) -> QualityProfile:
    """Fit and leave-one-document-out validate a small logistic calibrator."""

    if not samples or len({sample.document for sample in samples}) < 2:
        raise ValueError("calibration requires samples from at least two documents")
    families = sorted({document_family(sample.document) for sample in samples})
    predictions: list[tuple[float, bool]] = []
    accepted: list[tuple[float, bool]] = []
    boundaries: list[float] = []
    for held_out in families:
        training = [sample for sample in samples if document_family(sample.document) != held_out]
        inner_predictions: list[tuple[float, bool]] = []
        for validation_family in families:
            if validation_family == held_out:
                continue
            inner_training = [
                sample
                for sample in training
                if document_family(sample.document) != validation_family
            ]
            if not inner_training:
                continue
            weights, bias = _fit_logistic(inner_training, threshold)
            inner_predictions.extend(
                (
                    _logistic_score(weights, bias, sample.features),
                    sample.actual_quality >= 100.0,
                )
                for sample in training
                if document_family(sample.document) == validation_family
            )
        boundary = _safe_boundary(inner_predictions, max_false_accept_rate=0.0)
        boundaries.append(boundary)
        if not training:
            continue
        coefficients, intercept = _fit_logistic(training, threshold)
        for sample in samples:
            if document_family(sample.document) == held_out:
                score = _logistic_score(coefficients, intercept, sample.features)
                result = (score, sample.actual_quality >= 100.0)
                predictions.append(result)
                if score >= boundary:
                    accepted.append(result)
    boundary = max(boundaries, default=100.0)
    false_accepts = sum(not actual for _, actual in accepted)
    false_accept_rate = false_accepts / len(accepted) if accepted else 0.0
    promoted = (
        len(families) >= 3
        and bool(accepted)
        and false_accepts == 0
        and all(sample.human_verified for sample in samples)
    )
    documents = {sample.document for sample in samples}
    coefficients, intercept = _fit_logistic(samples, threshold)
    return QualityProfile(
        profile_version=4,
        feature_names=FEATURE_NAMES,
        coefficients=coefficients,
        intercept=intercept,
        threshold=threshold,
        prompt_hashes=prompt_hashes,
        groundtruth_hashes=groundtruth_hashes,
        validation={
            "promotion_passed": int(promoted),
            "held_out_family_count": len(families),
            "human_verified": int(all(sample.human_verified for sample in samples)),
            "correctness_boundary": 100.0,
            "sample_count": len(samples),
            "document_count": len(documents),
            "accepted_count": len(accepted),
            "false_accept_count": false_accepts,
            "false_accept_rate": false_accept_rate,
            "acceptance_rate": len(accepted) / max(1, len(predictions)),
            "raw_decision_boundary": boundary,
        },
        raw_decision_boundary=boundary,
        model_id=model_id,
        reasoning_effort=reasoning_effort,
    )


def _safe_boundary(predictions: list[tuple[float, bool]], *, max_false_accept_rate: float) -> float:
    """Maximize accepted coverage while keeping observed false accepts within the cap."""

    for boundary in sorted({score for score, _ in predictions}):
        accepted = [actual for score, actual in predictions if score >= boundary]
        if accepted:
            false_accept_rate = sum(not actual for actual in accepted) / len(accepted)
            if false_accept_rate <= max_false_accept_rate:
                return boundary
    return 100.0


def _fit_logistic(
    samples: list[CalibrationSample], threshold: float
) -> tuple[tuple[float, ...], float]:
    coefficients = [0.0] * len(FEATURE_NAMES)
    positive_rate = sum(sample.actual_quality >= 100.0 for sample in samples) / len(samples)
    positive_rate = min(0.999, max(0.001, positive_rate))
    intercept = math.log(positive_rate / (1 - positive_rate))
    learning_rate = 0.3
    regularization = 0.02
    for _ in range(2_000):
        gradients = [0.0] * len(coefficients)
        intercept_gradient = 0.0
        for sample in samples:
            target = float(sample.actual_quality >= 100.0)
            prediction = _sigmoid(
                intercept
                + sum(
                    coefficient * feature
                    for coefficient, feature in zip(coefficients, sample.features, strict=True)
                )
            )
            error = prediction - target
            intercept_gradient += error
            for index, feature in enumerate(sample.features):
                gradients[index] += error * feature
        size = len(samples)
        intercept -= learning_rate * intercept_gradient / size
        for index in range(len(coefficients)):
            gradient = gradients[index] / size + regularization * coefficients[index]
            coefficients[index] -= learning_rate * gradient
    return tuple(coefficients), intercept


def _logistic_score(
    coefficients: tuple[float, ...], intercept: float, features: tuple[float, ...]
) -> float:
    value = intercept + sum(
        coefficient * feature for coefficient, feature in zip(coefficients, features, strict=True)
    )
    return _sigmoid(value) * 100


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1 + exp_value)


def _all_groundings(element: DraftElement):
    yield element.grounding
    if isinstance(element, DraftTable):
        for cell in element.children:
            yield cell.grounding
            yield from cell.atomic_grounding
    else:
        yield from element.atomic_grounding


def _box_score(element: DraftElement) -> float:
    groundings = list(_all_groundings(element))
    valid = sum(
        grounding.box.xmax > grounding.box.xmin and grounding.box.ymax > grounding.box.ymin
        for grounding in groundings
    )
    return valid / max(1, len(groundings))


def _ocr_cleanliness(text: str) -> float:
    if not text:
        return 0.0
    anomaly_count = text.count("[ILLEGIBLE_TEXT]") + text.count("\ufffd")
    anomaly_count += len(re.findall(r"(.)\1{7,}", text))
    anomaly_count += sum(ord(char) < 32 and char not in "\n\t" for char in text)
    return max(0.0, 1.0 - anomaly_count / max(1, len(text) / 40))


def _table_consistency(element: DraftElement, text: str) -> float:
    if not isinstance(element, DraftTable):
        return 1.0
    occupied: set[tuple[int, int]] = set()
    for cell in element.children:
        for row in range(cell.row, cell.row + cell.rowspan):
            for col in range(cell.col, cell.col + cell.colspan):
                if (row, col) in occupied:
                    return 0.0
                occupied.add((row, col))
    html_cells = len(re.findall(r"<td(?:\s|>)", text, flags=re.IGNORECASE))
    return 1.0 if html_cells == len(element.children) and "<table" in text.lower() else 0.0


def _finding_score(audit: SegmentAudit) -> float:
    penalty = {"low": 0.1, "medium": 0.35, "high": 0.7}
    return max(0.0, 1.0 - sum(penalty[item.severity] for item in audit.findings))


def profile_json(profile: QualityProfile) -> str:
    return json.dumps(profile.model_dump(mode="json"), indent=2, sort_keys=True)
