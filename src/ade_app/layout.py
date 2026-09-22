"""Strict PP-StructureV3 layout adapter and OpenCV form geometry proposals.

Owns the process-wide PP-StructureV3 model lifecycle (lazy init, GPU-with-CPU-fallback,
permanent failure latching) and turns its raw, version-varying output into the strict
LayoutRegion/GeometryProposal schemas the rest of the app relies on. Box coordinates
throughout this file are fractions of page width/height (0-1), not pixels; see _box.
Must not silently invent regions/proposals when the model or geometry pass fails —
failures become LayoutIssue entries and a "partial"/"unavailable" status instead.
Next: hybrid.py, which reads LayoutAnalysis and decide_routes() to pick local-vs-model
routing; preprocessing.py for how PreparedPage is produced upstream.
"""

from __future__ import annotations

import io
import math
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pydantic import Field, model_validator

from ade_app.config import LayoutSettings
from ade_app.models import Box, StrictModel
from ade_app.preprocessing import PageTransform, PreparedPage
from ade_app.raster import RenderedCrop, RenderedPage, crop_segment

# Two locks with different jobs: _MODEL_LOCK guards one-time lazy model creation
# (double-checked locking in _model()); _INFERENCE_LOCK separately serializes every
# predict() call afterward, because the underlying Paddle model is not assumed safe
# for concurrent inference even once it exists. _MODEL_FAILURE and _CPU_LATCHED are
# process-lifetime latches: once set they are never cleared, so a permanent init
# failure or a GPU->CPU fallback both stick for every later document until restart.
_MODEL_LOCK = Lock()
_INFERENCE_LOCK = Lock()
_MODEL: Any | None = None
_MODEL_DEVICE = "uninitialized"
_MODEL_FAILURE: LayoutIssue | None = None
_CPU_LATCHED = False
_DEVICE_SETTINGS = LayoutSettings()

LayoutCategory = Literal["text", "table", "form_field", "checkbox", "handwriting", "other"]
LayoutDevice = Literal["gpu:0", "cpu"]
LayoutIssueCode = Literal[
    "dependency_unavailable",
    "model_unavailable",
    "model_corrupt",
    "model_incompatible",
    "model_init_failed",
    "accelerator_failed",
    "inference_failed",
    "invalid_output",
    "geometry_failed",
    "unexpected_adapter_error",
]
LayoutStage = Literal["model_init", "inference", "result_decode", "region_parse", "geometry"]


class LayoutIssue(StrictModel):
    code: LayoutIssueCode
    stage: LayoutStage
    message: str = Field(min_length=1)
    cause_type: str = Field(min_length=1)
    attempted_devices: tuple[LayoutDevice, ...] = ()
    recovered: bool = False

    @model_validator(mode="after")
    def validate_devices(self) -> LayoutIssue:
        if len(self.attempted_devices) > 2 or len(set(self.attempted_devices)) != len(
            self.attempted_devices
        ):
            raise ValueError("attempted_devices must contain at most one GPU and one CPU")
        return self


class RegionCrop(StrictModel):
    """Context crop in cleaned-page coordinates; pixels stay in memory only."""

    png_bytes: bytes = Field(min_length=1, exclude=True, repr=False)
    box: Box
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class LayoutRegion(StrictModel):
    region_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    category: LayoutCategory = "other"
    confidence: float = Field(ge=0, le=1)
    confidence_source: Literal["pp_structure", "opencv_geometry"] = "pp_structure"
    box: Box
    prepared_box: Box | None = None
    route: Literal["local_text", "local_table", "verification"]
    text: str | None = None
    html: str | None = None
    markdown: str | None = None
    table_is_simple: bool | None = None
    crop: RegionCrop | None = None

    @model_validator(mode="before")
    @classmethod
    def derive_category(cls, value: Any) -> Any:
        if isinstance(value, dict) and "category" not in value:
            value = dict(value)
            value["category"] = _category(str(value.get("kind", "")))
        return value

    @model_validator(mode="after")
    def validate_content(self) -> LayoutRegion:
        if self.route == "local_text" and (self.category != "text" or not _nonblank(self.text)):
            raise ValueError("local_text regions require nonblank text")
        if self.route == "local_table" and (self.category != "table" or not _nonblank(self.html)):
            raise ValueError("local_table regions require table HTML")
        if (
            self.category in {"form_field", "checkbox", "handwriting", "other"}
            and self.route != "verification"
        ):
            raise ValueError(f"{self.category} regions must route to the visual model")
        if self.category != "table" and any(
            item is not None for item in (self.html, self.markdown, self.table_is_simple)
        ):
            raise ValueError("table content is only valid for table regions")
        if self.markdown is not None and (
            not _nonblank(self.html) or self.table_is_simple is not True or self.confidence < 0.85
        ):
            raise ValueError("table Markdown requires simple HTML and confidence >= 0.85")
        if self.crop is not None and self.category not in {
            "handwriting",
            "checkbox",
            "form_field",
        }:
            raise ValueError("crops are limited to handwriting, checkbox, and form-field regions")
        for item in (self.text, self.html, self.markdown):
            if item is not None and not item.strip():
                raise ValueError("optional region text must not be blank")
        return self


class GeometryProposal(StrictModel):
    proposal_id: str = Field(min_length=1)
    kind: Literal["checkbox", "form_line"]
    category: Literal["checkbox", "form_field"] = "form_field"
    confidence: float = Field(ge=0, le=1)
    box: Box
    prepared_box: Box | None = None
    crop: RegionCrop | None = None

    @model_validator(mode="before")
    @classmethod
    def derive_category(cls, value: Any) -> Any:
        if isinstance(value, dict) and "category" not in value:
            value = dict(value)
            value["category"] = "checkbox" if value.get("kind") == "checkbox" else "form_field"
        return value


class LayoutAnalysis(StrictModel):
    source_page: int = Field(ge=1)
    regions: list[LayoutRegion]
    proposals: list[GeometryProposal]
    status: Literal["complete", "partial", "unavailable"] = "complete"
    device: LayoutDevice | None = None
    attempted_devices: tuple[LayoutDevice, ...] = ()
    issues: list[LayoutIssue] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_result(self) -> LayoutAnalysis:
        ids = [item.region_id for item in self.regions] + [
            item.proposal_id for item in self.proposals
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("layout IDs must be unique")
        unrecovered = any(not issue.recovered for issue in self.issues)
        has_output = bool(self.regions or self.proposals)
        if self.status == "complete" and unrecovered:
            raise ValueError("complete layout analysis cannot contain unrecovered issues")
        if self.status == "partial" and (not has_output or not unrecovered):
            raise ValueError("partial layout analysis requires output and an unrecovered issue")
        if self.status == "unavailable" and (has_output or not unrecovered):
            raise ValueError(
                "unavailable layout analysis requires no output and an unrecovered issue"
            )
        if len(self.attempted_devices) > 2 or len(set(self.attempted_devices)) != len(
            self.attempted_devices
        ):
            raise ValueError("attempted_devices must be ordered and unique")
        return self


@dataclass(frozen=True, slots=True)
class RouteDecision:
    analysis: LayoutAnalysis | None
    use_full_page_model: bool
    reason: str


class _ModelUnavailable(RuntimeError):
    def __init__(self, issue: LayoutIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


class PPStructureAnalyzer:
    """Run one process-wide PP-Structure model with bounded CPU fallback."""

    def __init__(self, settings: LayoutSettings | None = None) -> None:
        global _DEVICE_SETTINGS
        requested = settings or LayoutSettings()
        # _MODEL is a process-wide singleton (see module globals above), so a second
        # analyzer instance cannot silently reconfigure a model that already exists.
        if _MODEL is not None and requested != _DEVICE_SETTINGS:
            raise RuntimeError("layout device settings cannot change after model initialization")
        _DEVICE_SETTINGS = requested

    def analyze(self, prepared: PreparedPage) -> LayoutAnalysis:
        issues: list[LayoutIssue] = []
        try:
            proposals = _geometry_proposals(prepared)
        except Exception as error:
            proposals = []
            issues.append(_issue(error, "geometry_failed", "geometry"))

        try:
            layout_page = _bounded_layout_page(prepared)
            predictions, device, attempted, recovered = _predict(layout_page)
            if recovered is not None:
                issues.append(recovered)
        except _ModelUnavailable as error:
            issues.append(error.issue)
            attempted = error.issue.attempted_devices
            device = attempted[-1] if attempted else None
            return _analysis(prepared, [], proposals, device, attempted, issues)
        except Exception as error:
            attempted = _attempted_devices()
            issues.append(_issue(error, "unexpected_adapter_error", "inference", attempted))
            return _analysis(prepared, [], proposals, None, attempted, issues)

        if not predictions:
            issues.append(
                LayoutIssue(
                    code="invalid_output",
                    stage="result_decode",
                    message="PP-StructureV3 returned no page result",
                    cause_type="EmptyPrediction",
                    attempted_devices=attempted,
                )
            )
            return _analysis(prepared, [], proposals, device, attempted, issues)
        try:
            regions = _regions(_payload(predictions[0]), layout_page)
            for region in regions:
                if region.crop is not None and region.prepared_box is not None:
                    region.crop = _crop(prepared.page, region.prepared_box)
            proposals = _suppress_table_lines(proposals, regions)
        except Exception as error:
            issues.append(_issue(error, "invalid_output", "region_parse", attempted))
            regions = []
        if not regions and not proposals and not any(not item.recovered for item in issues):
            issues.append(
                LayoutIssue(
                    code="invalid_output",
                    stage="region_parse",
                    message="PP-StructureV3 returned no usable regions",
                    cause_type="EmptyLayout",
                    attempted_devices=attempted,
                )
            )
        return _analysis(prepared, regions, proposals, device, attempted, issues)


def _bounded_layout_page(prepared: PreparedPage) -> PreparedPage:
    """Bound native layout memory while retaining full-resolution verification pixels."""
    import numpy as np
    from PIL import Image

    page = prepared.page
    if max(page.width, page.height) <= 1600:
        return prepared
    with Image.open(io.BytesIO(page.png_bytes)) as image:
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="PNG")
        width, height = image.size
    # This composes a second coordinate-space transform on top of whatever preprocessing
    # already applied (prepared.transform): PP-Structure only ever sees this shrunk page,
    # but every LayoutRegion/GeometryProposal keeps both `prepared_box` (in this shrunk
    # inference space, for crop/OCR joining against the same payload) and `box` (rebased
    # through the combined forward/inverse transform back to the original full-resolution
    # page, via prepared.box_to_original) so downstream code never has to know which
    # resolution the model actually ran at.
    scale = np.diag([width / page.width, height / page.height, 1.0])
    forward = scale @ np.asarray(prepared.transform.forward)
    inverse = np.asarray(prepared.transform.inverse) @ np.linalg.inv(scale)
    return replace(
        prepared,
        page=replace(page, png_bytes=output.getvalue(), width=width, height=height),
        transform=PageTransform(
            forward=forward.tolist(),
            inverse=inverse.tolist(),
            operations=prepared.transform.operations,
        ),
    )


def decide_routes(analysis: LayoutAnalysis | None, calibrated_routes: set[str]) -> RouteDecision:
    """Fail closed: only complete, calibrated local routes may bypass the model cascade."""

    if analysis is None:
        return RouteDecision(None, True, "layout_failure")
    if analysis.status != "complete":
        codes = ",".join(sorted({item.code for item in analysis.issues if not item.recovered}))
        return RouteDecision(analysis, True, f"layout_{analysis.status}:{codes}")
    required: set[str] = {
        region.route for region in analysis.regions if region.route != "verification"
    }
    if analysis.proposals:
        required.add("verification")
    missing = required - calibrated_routes
    if missing:
        return RouteDecision(analysis, True, "uncalibrated_routes:" + ",".join(sorted(missing)))
    if any(region.route == "verification" for region in analysis.regions) or analysis.proposals:
        return RouteDecision(analysis, True, "ambiguous_regions_require_model")
    return RouteDecision(analysis, False, "all_regions_locally_accepted")


def _analysis(
    prepared: PreparedPage,
    regions: list[LayoutRegion],
    proposals: list[GeometryProposal],
    device: LayoutDevice | None,
    attempted: tuple[LayoutDevice, ...],
    issues: list[LayoutIssue],
) -> LayoutAnalysis:
    unrecovered = any(not item.recovered for item in issues)
    status = (
        "partial"
        if unrecovered and (regions or proposals)
        else "unavailable"
        if unrecovered
        else "complete"
    )
    return LayoutAnalysis(
        source_page=prepared.page.source_page,
        regions=regions,
        proposals=proposals,
        status=status,
        device=device,
        attempted_devices=attempted,
        issues=issues,
    )


def _predict(
    prepared: PreparedPage,
) -> tuple[list[Any], LayoutDevice, tuple[LayoutDevice, ...], LayoutIssue | None]:
    # PP-StructureV3's predict() takes a file path, not in-memory bytes, hence the
    # temp-file round trip; the directory (and file) is removed on every exit path.
    with tempfile.TemporaryDirectory(prefix="ade-layout-") as directory:
        path = Path(directory) / "page.png"
        path.write_bytes(prepared.page.png_bytes)
        with _INFERENCE_LOCK:
            model, device, attempted = _model()
            try:
                return list(model.predict(input=str(path))), device, attempted, None
            except Exception as error:
                if (
                    device == "cpu"
                    or not _DEVICE_SETTINGS.allow_cpu_fallback
                    or not _is_accelerator_error(error)
                ):
                    issue = _inference_issue(error, attempted)
                    _cache_permanent_failure(issue)
                    raise _ModelUnavailable(issue) from error
                # Inference-time fallback: the model initialized fine on GPU earlier but
                # this call's inference failed with what looks like an accelerator error.
                # _switch_to_cpu (distinct from the init-time fallback in _model() below)
                # rebuilds the singleton on CPU and latches it there for the rest of the
                # process; only the current page's prediction is retried, once.
                model = _switch_to_cpu()
                attempted = ("gpu:0", "cpu")
                try:
                    predictions = list(model.predict(input=str(path)))
                except Exception as cpu_error:
                    issue = _inference_issue(cpu_error, attempted)
                    _cache_permanent_failure(issue)
                    raise _ModelUnavailable(issue) from cpu_error
                recovered = LayoutIssue(
                    code="accelerator_failed",
                    stage="inference",
                    message="GPU inference failed; PP-StructureV3 continued on CPU",
                    cause_type=type(error).__name__,
                    attempted_devices=attempted,
                    recovered=True,
                )
                return predictions, "cpu", attempted, recovered


def _model() -> tuple[Any, LayoutDevice, tuple[LayoutDevice, ...]]:
    global _MODEL, _MODEL_DEVICE, _MODEL_FAILURE
    # Double-checked locking: the outer check avoids taking _MODEL_LOCK on the common
    # path once the model exists; the inner check (repeated after acquiring the lock)
    # is the real guard against two threads both creating the model. A permanent
    # _MODEL_FAILURE, once cached by _cache_permanent_failure, makes every future call
    # fail fast without retrying model creation for the rest of the process.
    if _MODEL_FAILURE is not None:
        raise _ModelUnavailable(_MODEL_FAILURE)
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL_FAILURE is not None:
                raise _ModelUnavailable(_MODEL_FAILURE)
            if _MODEL is None:
                try:
                    from paddleocr import PPStructureV3
                except (ImportError, OSError) as error:
                    issue = _issue(error, "dependency_unavailable", "model_init")
                    _MODEL_FAILURE = issue
                    raise _ModelUnavailable(issue) from error
                if _DEVICE_SETTINGS.device == "gpu" and not _cuda_available():
                    issue = _issue(
                        RuntimeError("GPU was required but CUDA is unavailable"),
                        "accelerator_failed",
                        "model_init",
                        ("gpu:0",),
                    )
                    raise _ModelUnavailable(issue)
                device: LayoutDevice = (
                    "cpu"
                    if _DEVICE_SETTINGS.device == "cpu" or _CPU_LATCHED or not _cuda_available()
                    else "gpu:0"
                )
                attempted: tuple[LayoutDevice, ...] = (device,)
                try:
                    _MODEL = _create_model(PPStructureV3, device)
                    _MODEL_DEVICE = device
                except Exception as error:
                    if (
                        device == "gpu:0"
                        and _DEVICE_SETTINGS.allow_cpu_fallback
                        and _is_accelerator_error(error)
                    ):
                        attempted = ("gpu:0", "cpu")
                        try:
                            _MODEL = _create_model(PPStructureV3, "cpu")
                            _MODEL_DEVICE = "cpu"
                            _latch_cpu()
                        except Exception as cpu_error:
                            issue = _model_issue(cpu_error, attempted)
                            _MODEL_FAILURE = issue
                            raise _ModelUnavailable(issue) from cpu_error
                    else:
                        issue = _model_issue(error, attempted)
                        _MODEL_FAILURE = issue
                        raise _ModelUnavailable(issue) from error
                return _MODEL, _device(), attempted
    return _MODEL, _device(), (_device(),)


def _create_model(factory: Any, device: LayoutDevice) -> Any:
    # Paddle 3.3's Windows oneDNN backend cannot execute the layout model's
    # PIR array attributes. Use the standard CPU kernels on that platform.
    options = {"enable_mkldnn": False} if device == "cpu" and sys.platform == "win32" else {}
    return factory(
        device=device,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=True,
        **options,
    )


def _cuda_available() -> bool:
    try:
        import paddle

        return bool(paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count())
    except Exception:
        return False


def _is_accelerator_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(token in message for token in ("cuda", "cudnn", "gpu", "out of memory"))


def _device() -> LayoutDevice:
    if _MODEL_DEVICE not in {"gpu:0", "cpu"}:
        raise RuntimeError("PP-StructureV3 model device is unavailable")
    return _MODEL_DEVICE


def _attempted_devices() -> tuple[LayoutDevice, ...]:
    return () if _MODEL_DEVICE == "uninitialized" else (_device(),)


def _latch_cpu() -> None:
    global _CPU_LATCHED
    _CPU_LATCHED = True


def _switch_to_cpu() -> Any:
    global _MODEL, _MODEL_DEVICE, _MODEL_FAILURE
    with _MODEL_LOCK:
        if _MODEL_DEVICE != "cpu":
            try:
                from paddleocr import PPStructureV3
            except (ImportError, OSError) as error:
                raise _ModelUnavailable(
                    _issue(error, "dependency_unavailable", "model_init", ("gpu:0", "cpu"))
                ) from error
            try:
                _MODEL = _create_model(PPStructureV3, "cpu")
            except Exception as error:
                issue = _model_issue(error, ("gpu:0", "cpu"))
                _MODEL_FAILURE = issue
                raise _ModelUnavailable(issue) from error
            _MODEL_DEVICE = "cpu"
            _latch_cpu()
    return _MODEL


def _payload(prediction: Any) -> dict[str, Any]:
    # PP-StructureV3's prediction result shape is not fixed: it can be the payload
    # dict directly, an object exposing it via a `.json` attribute or callable, or a
    # dict wrapped one level under a "res" key. This normalizes all three.
    value = getattr(prediction, "json", prediction)
    if callable(value):
        value = value()
    if isinstance(value, dict) and isinstance(value.get("res"), dict):
        value = value["res"]
    if not isinstance(value, dict):
        raise ValueError("PP-StructureV3 result has an unsupported shape")
    return value


def _regions(payload: dict[str, Any], prepared: PreparedPage) -> list[LayoutRegion]:
    raw_regions = payload.get("parsing_res_list")
    if not isinstance(raw_regions, list):
        layout = payload.get("layout_det_res") or {}
        raw_regions = layout.get("boxes") if isinstance(layout, dict) else []
    if not isinstance(raw_regions, list):
        raw_regions = []

    tables = _table_candidates(payload, prepared)
    used_tables: set[int] = set()
    regions: list[LayoutRegion] = []
    for index, raw in enumerate(raw_regions):
        if not isinstance(raw, dict):
            continue
        coordinate = (
            raw.get("block_bbox") or raw.get("coordinate") or raw.get("bbox") or raw.get("box")
        )
        prepared_box = _box(coordinate, prepared)
        if prepared_box is None:
            continue
        raw_label = str(raw.get("block_label") or raw.get("label") or "unknown")
        category = _category(raw_label)
        confidence = _confidence(
            raw.get("score", raw.get("confidence", _layout_confidence(payload, raw)))
        )
        text = raw.get("block_content") or raw.get("text") or _ocr_text(payload, coordinate)
        html: str | None = None
        markdown: str | None = None
        table_is_simple: bool | None = None
        if category == "table":
            table = _match_table(prepared_box, tables, used_tables)
            html_value = raw.get("pred_html") or (table[1].get("pred_html") if table else None)
            html = str(html_value).strip() if html_value and str(html_value).strip() else None
            if table is not None:
                used_tables.add(table[0])
            table_is_simple, markdown = _table_markdown(html, confidence)
        if category == "table" and html:
            route: Literal["local_text", "local_table", "verification"] = "local_table"
        elif category == "text" and text and str(text).strip():
            route = "local_text"
        else:
            route = "verification"
        regions.append(
            LayoutRegion(
                region_id=f"p{prepared.page.source_page}-r{index}",
                kind=_normalize_label(raw_label),
                category=category,
                confidence=confidence,
                box=prepared.box_to_original(prepared_box),
                prepared_box=prepared_box,
                route=route,
                text=str(text).strip() if text and str(text).strip() else None,
                html=html,
                markdown=markdown,
                table_is_simple=table_is_simple,
                crop=(
                    _crop(prepared.page, prepared_box)
                    if category in {"handwriting", "checkbox", "form_field"}
                    else None
                ),
            )
        )
    return regions


def _layout_confidence(payload: dict[str, Any], block: dict[str, Any]) -> float:
    """Join parsed blocks to detector scores only when the match is unique."""
    coordinate = block.get("block_bbox")
    layout = payload.get("layout_det_res")
    if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 4:
        return 0.0
    if not isinstance(layout, dict) or not isinstance(layout.get("boxes"), list):
        return 0.0
    matches = []
    for box in layout["boxes"]:
        if not isinstance(box, dict) or box.get("label") != block.get("block_label"):
            continue
        detected = box.get("coordinate")
        if not isinstance(detected, (list, tuple)) or len(detected) != 4:
            continue
        try:
            same_box = all(
                abs(float(a) - float(b)) <= 1 for a, b in zip(coordinate, detected, strict=True)
            )
        except (TypeError, ValueError):
            continue
        if same_box:
            matches.append(_confidence(box.get("score")))
    return matches[0] if len(matches) == 1 else 0.0


def _table_candidates(
    payload: dict[str, Any], prepared: PreparedPage
) -> list[tuple[int, dict[str, Any], Box | None]]:
    tables = payload.get("table_res_list") or []
    if not isinstance(tables, list):
        return []
    result: list[tuple[int, dict[str, Any], Box | None]] = []
    for index, table in enumerate(tables):
        if not isinstance(table, dict):
            continue
        coordinate = table.get("bbox") or table.get("coordinate") or table.get("box")
        if coordinate is None:
            coordinate = _union_coordinates(table.get("cell_box_list"))
        result.append((index, table, _box(coordinate, prepared)))
    return result


def _match_table(
    region: Box,
    candidates: list[tuple[int, dict[str, Any], Box | None]],
    used: set[int],
) -> tuple[int, dict[str, Any], Box | None] | None:
    ranked: list[tuple[float, float, int, dict[str, Any], Box | None]] = []
    for index, table, box in candidates:
        if index in used or box is None:
            continue
        iou, containment = _overlap(region, box)
        if iou >= 0.35 or containment >= 0.80:
            ranked.append((iou, containment, index, table, box))
    if not ranked:
        remaining = [item for item in candidates if item[0] not in used]
        return remaining[0] if len(remaining) == 1 and remaining[0][2] is None else None
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    best = ranked[0]
    if len(ranked) > 1 and abs((best[0] + best[1]) - (ranked[1][0] + ranked[1][1])) < 0.05:
        return None
    return best[2], best[3], best[4]


def _ocr_text(payload: dict[str, Any], region: Any) -> str | None:
    if not isinstance(region, (list, tuple)) or len(region) != 4:
        return None
    ocr = payload.get("overall_ocr_res") or {}
    if not isinstance(ocr, dict):
        return None
    polygons = ocr.get("rec_polys") or ocr.get("dt_polys") or []
    texts = ocr.get("rec_texts") or []
    left, top, right, bottom = map(float, region)
    selected: list[str] = []
    for polygon, text in zip(polygons, texts, strict=False):
        if not isinstance(polygon, (list, tuple)) or not polygon:
            continue
        try:
            center_x = sum(float(point[0]) for point in polygon) / len(polygon)
            center_y = sum(float(point[1]) for point in polygon) / len(polygon)
        except (TypeError, ValueError, IndexError, ZeroDivisionError):
            continue
        if left <= center_x <= right and top <= center_y <= bottom and str(text).strip():
            selected.append(str(text).strip())
    return "\n".join(selected) or None


def _box(value: Any, prepared: PreparedPage) -> Box | None:
    """Convert a pixel [left, top, right, bottom] rect into a page-fraction Box.

    Every Box in this module is normalized to [0, 1] as a fraction of prepared page
    width/height, not pixels — callers must have the matching PreparedPage to make
    sense of the fraction. Boxes under 1 pixel wide/tall after clamping to the page
    are rejected rather than kept as degenerate zero-area regions.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        left, top, right, bottom = map(float, value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in (left, top, right, bottom)):
        return None
    left, right = max(0.0, left), min(float(prepared.page.width), right)
    top, bottom = max(0.0, top), min(float(prepared.page.height), bottom)
    if right - left < 1 or bottom - top < 1:
        return None
    return Box(
        xmin=round(left / prepared.page.width, 5),
        ymin=round(top / prepared.page.height, 5),
        xmax=round(right / prepared.page.width, 5),
        ymax=round(bottom / prepared.page.height, 5),
    )


def _geometry_proposals(prepared: PreparedPage) -> list[GeometryProposal]:
    """Heuristic OpenCV contour geometry, not a trained model.

    Near-square contours with a mid-range fill ratio are candidate checkboxes; long,
    thin, wide contours are candidate form/signature lines. The size/ratio/fill
    thresholds below are calibrated by hand against typical scanned forms, not
    derived from data, and are rescaled by `scale` for pages rendered at other than
    300 DPI.
    """
    from ade_app.preprocessing import _opencv

    cv2, np = _opencv()
    image = cv2.imdecode(np.frombuffer(prepared.page.png_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError("cleaned page could not be decoded for geometry analysis")
    binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    scale = max(0.5, min(2.0, (prepared.page.effective_dpi or 300.0) / 300.0))
    candidates: list[tuple[Literal["checkbox", "form_line"], float, Box]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        ratio = width / max(1, height)
        if (
            8 * scale <= width <= 80 * scale
            and 8 * scale <= height <= 80 * scale
            and 0.75 <= ratio <= 1.25
        ):
            area_ratio = cv2.contourArea(contour) / max(1, width * height)
            if 0.08 <= area_ratio <= 0.9:
                squareness = 1.0 - min(1.0, abs(1.0 - ratio))
                fill = 1.0 - min(1.0, abs(0.35 - area_ratio) / 0.35)
                confidence = round(max(0.25, min(0.85, 0.35 + 0.3 * squareness + 0.2 * fill)), 4)
                candidates.append(
                    ("checkbox", confidence, _pixel_box(x, y, width, height, prepared))
                )
        elif width >= max(80 * scale, prepared.page.width / 12) and height <= 8 * scale:
            length_score = min(1.0, width / max(1.0, prepared.page.width / 3))
            confidence = round(max(0.25, min(0.75, 0.3 + 0.4 * length_score)), 4)
            candidates.append(
                ("form_line", confidence, _pixel_box(x, y, width, max(height, 1), prepared))
            )

    kept: list[tuple[Literal["checkbox", "form_line"], float, Box]] = []
    for candidate in sorted(candidates, key=lambda item: (-item[1], item[2].ymin, item[2].xmin)):
        if any(
            candidate[0] == prior[0] and _overlap(candidate[2], prior[2])[1] >= 0.8
            for prior in kept
        ):
            continue
        kept.append(candidate)
        if len(kept) >= 128:
            break

    result: list[GeometryProposal] = []
    for kind, confidence, prepared_box in kept:
        crop_box = _expanded_form_box(prepared_box) if kind == "form_line" else prepared_box
        result.append(
            GeometryProposal(
                proposal_id=f"p{prepared.page.source_page}-g{len(result)}",
                kind=kind,
                category="checkbox" if kind == "checkbox" else "form_field",
                confidence=confidence,
                box=prepared.box_to_original(prepared_box),
                prepared_box=prepared_box,
                crop=_crop(prepared.page, crop_box),
            )
        )
    return result


def _suppress_table_lines(
    proposals: list[GeometryProposal], regions: list[LayoutRegion]
) -> list[GeometryProposal]:
    # Table rulings are frequently mistaken for form_line proposals by the OpenCV
    # heuristic above; once PP-Structure has identified the real table regions, drop
    # any form_line proposal that heavily overlaps one rather than double-reporting it.
    table_boxes = [item.box for item in regions if item.category == "table"]
    return [
        item
        for item in proposals
        if item.kind != "form_line"
        or not any(_overlap(item.box, table_box)[1] >= 0.8 for table_box in table_boxes)
    ]


def _pixel_box(x: int, y: int, width: int, height: int, prepared: PreparedPage) -> Box:
    return Box(
        xmin=round(x / prepared.page.width, 5),
        ymin=round(y / prepared.page.height, 5),
        xmax=round(min(prepared.page.width, x + width) / prepared.page.width, 5),
        ymax=round(min(prepared.page.height, y + height) / prepared.page.height, 5),
    )


def _expanded_form_box(box: Box) -> Box:
    """Pad a bare form-line box into a crop that includes its label.

    The detected line itself carries no text; a human/model reviewing the crop needs
    the label above it to know what the field means, so the box is expanded asymmetrically
    (3x its height upward, 1x downward, a small horizontal margin) rather than uniformly.
    """
    height = max(0.03, box.ymax - box.ymin)
    return Box(
        xmin=max(0.0, round(box.xmin - 0.03, 5)),
        ymin=max(0.0, round(box.ymin - height * 3, 5)),
        xmax=min(1.0, round(box.xmax + 0.03, 5)),
        ymax=min(1.0, round(box.ymax + height, 5)),
    )


def _crop(page: RenderedPage, box: Box) -> RegionCrop:
    rendered = crop_segment(page, box)
    return RegionCrop(
        png_bytes=rendered.png_bytes,
        box=_rendered_crop_box(rendered),
        width=rendered.width,
        height=rendered.height,
    )


def _rendered_crop_box(crop: RenderedCrop) -> Box:
    return Box(
        xmin=round(crop.left / crop.page_width, 5),
        ymin=round(crop.top / crop.page_height, 5),
        xmax=round((crop.left + crop.width) / crop.page_width, 5),
        ymax=round((crop.top + crop.height) / crop.page_height, 5),
    )


def _category(label: str) -> LayoutCategory:
    aliases: dict[str, LayoutCategory] = {
        "text": "text",
        "paragraph": "text",
        "title": "text",
        "doc_title": "text",
        "paragraph_title": "text",
        "heading": "text",
        "header": "text",
        "footer": "text",
        "table": "table",
        "form": "form_field",
        "form_field": "form_field",
        "input_field": "form_field",
        "checkbox": "checkbox",
        "check_box": "checkbox",
        "handwriting": "handwriting",
        "handwritten": "handwriting",
        "handwritten_text": "handwriting",
        "signature": "handwriting",
    }
    return aliases.get(_normalize_label(label), "other")


def _normalize_label(label: str) -> str:
    normalized = unicodedata.normalize("NFKC", label).casefold()
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_") or "unknown"


def _confidence(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(1.0, score)), 5) if math.isfinite(score) else 0.0


def _union_coordinates(values: Any) -> list[float] | None:
    if not isinstance(values, list) or not values:
        return None
    coordinates: list[tuple[float, float, float, float]] = []
    for value in values:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            continue
        try:
            left, top, right, bottom = map(float, value)
            coordinates.append((left, top, right, bottom))
        except (TypeError, ValueError):
            continue
    if not coordinates:
        return None
    return [
        min(item[0] for item in coordinates),
        min(item[1] for item in coordinates),
        max(item[2] for item in coordinates),
        max(item[3] for item in coordinates),
    ]


def _overlap(left: Box, right: Box) -> tuple[float, float]:
    width = max(0.0, min(left.xmax, right.xmax) - max(left.xmin, right.xmin))
    height = max(0.0, min(left.ymax, right.ymax) - max(left.ymin, right.ymin))
    intersection = width * height
    left_area = (left.xmax - left.xmin) * (left.ymax - left.ymin)
    right_area = (right.xmax - right.xmin) * (right.ymax - right.ymin)
    union = left_area + right_area - intersection
    smaller = min(left_area, right_area)
    return intersection / union if union else 0.0, intersection / smaller if smaller else 0.0


class _SimpleTableParser(HTMLParser):
    """Deliberately rejecting HTML-table validator, not a general parser.

    `valid` stays True only for one flat table with no attributes, no merged cells
    (rowspan/colspan other than "1"), and no tags outside table/thead/tbody/tfoot/tr/
    td/th/br. `_table_markdown` uses `valid` to decide whether a Markdown rendering is
    safe to also produce — a false "simple" verdict would silently drop merged cells
    or nested structure when flattened to Markdown, so this errs toward rejecting.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.header_rows: list[bool] = []
        self.valid = True
        self.table_count = 0
        self.table_depth = 0
        self.row: list[str] | None = None
        self.row_headers: list[bool] = []
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.table_count += 1
            self.table_depth += 1
            self.valid = (
                self.valid and self.table_count == 1 and self.table_depth == 1 and not attrs
            )
        elif tag in {"html", "body"}:
            self.valid = self.valid and self.table_depth == 0 and not attrs
        elif tag in {"thead", "tbody", "tfoot"}:
            self.valid = self.valid and self.table_depth == 1 and not attrs
        elif tag == "tr":
            self.valid = self.valid and self.table_depth == 1 and self.row is None and not attrs
            self.row, self.row_headers = [], []
        elif tag in {"td", "th"}:
            self.valid = self.valid and self.row is not None and self.cell is None
            for name, value in attrs:
                if name not in {"rowspan", "colspan"} or value not in {None, "1"}:
                    self.valid = False
            self.cell = []
            self.row_headers.append(tag == "th")
        elif tag == "br":
            self.valid = self.valid and self.cell is not None and not attrs
            if self.cell is not None:
                self.cell.append("<br>")
        else:
            self.valid = False

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.handle_starttag(tag, attrs)
        else:
            self.valid = False

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"}:
            if self.cell is None or self.row is None:
                self.valid = False
                return
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr":
            if self.row is None or self.cell is not None:
                self.valid = False
                return
            self.rows.append(self.row)
            self.header_rows.append(bool(self.row_headers) and all(self.row_headers))
            self.row = None
        elif tag == "table":
            self.table_depth -= 1
            self.valid = self.valid and self.table_depth == 0
        elif tag not in {"html", "body", "thead", "tbody", "tfoot"}:
            self.valid = False

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)
        elif data.strip():
            self.valid = False


def _table_markdown(html: str | None, confidence: float) -> tuple[bool | None, str | None]:
    if html is None:
        return None, None
    parser = _SimpleTableParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return False, None
    widths = {len(row) for row in parser.rows}
    simple = (
        parser.valid
        and parser.table_count == 1
        and parser.table_depth == 0
        and parser.row is None
        and parser.cell is None
        and len(widths) == 1
        and next(iter(widths), 0) > 0
    )
    if not simple or confidence < 0.85:
        return simple, None
    rows = [[_markdown_cell(cell) for cell in row] for row in parser.rows]
    if parser.header_rows and parser.header_rows[0]:
        header, body = rows[0], rows[1:]
    else:
        header, body = ["" for _ in rows[0]], rows
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return True, "\n".join(lines)


def _markdown_cell(value: str) -> str:
    return " ".join(value.replace("\\", "\\\\").replace("|", "\\|").split())


def _model_issue(error: Exception, attempted: tuple[LayoutDevice, ...]) -> LayoutIssue:
    message = str(error).lower()
    if "dependency" in message or isinstance(error, ImportError):
        code: LayoutIssueCode = "dependency_unavailable"
        public = "PP-StructureV3 OCR dependencies are unavailable; install the project OCR extra"
    elif isinstance(error, (FileNotFoundError, ModuleNotFoundError)) or "not found" in message:
        code = "model_unavailable"
        public = "PP-StructureV3 model files are unavailable"
    elif any(token in message for token in ("checksum", "corrupt", "invalid model")):
        code = "model_corrupt"
        public = "PP-StructureV3 model files are corrupt"
    elif any(token in message for token in ("version", "incompatible", "unsupported")):
        code = "model_incompatible"
        public = "PP-StructureV3 model files are incompatible"
    else:
        code = "model_init_failed"
        public = "PP-StructureV3 could not be initialized"
    return LayoutIssue(
        code=code,
        stage="model_init",
        message=public,
        cause_type=type(error).__name__,
        attempted_devices=attempted,
    )


def _inference_issue(error: Exception, attempted: tuple[LayoutDevice, ...]) -> LayoutIssue:
    model_issue = _model_issue(error, attempted)
    if model_issue.code in {"model_unavailable", "model_corrupt", "model_incompatible"}:
        return model_issue.model_copy(update={"stage": "inference"})
    return _issue(error, "inference_failed", "inference", attempted)


def _cache_permanent_failure(issue: LayoutIssue) -> None:
    global _MODEL_FAILURE
    if issue.code in {"model_unavailable", "model_corrupt", "model_incompatible"}:
        _MODEL_FAILURE = issue


def _issue(
    error: Exception,
    code: LayoutIssueCode,
    stage: LayoutStage,
    attempted: tuple[LayoutDevice, ...] = (),
) -> LayoutIssue:
    messages: dict[LayoutIssueCode, str] = {
        "dependency_unavailable": "PP-StructureV3 dependencies are unavailable",
        "model_unavailable": "PP-StructureV3 model files are unavailable",
        "model_corrupt": "PP-StructureV3 model files are corrupt",
        "model_incompatible": "PP-StructureV3 model files are incompatible",
        "model_init_failed": "PP-StructureV3 could not be initialized",
        "accelerator_failed": "GPU processing failed; CPU will be used",
        "inference_failed": "PP-StructureV3 inference failed",
        "invalid_output": "PP-StructureV3 returned invalid output",
        "geometry_failed": "OpenCV region geometry analysis failed",
        "unexpected_adapter_error": "Layout analysis failed unexpectedly",
    }
    return LayoutIssue(
        code=code,
        stage=stage,
        message=messages[code],
        cause_type=type(error).__name__,
        attempted_devices=attempted,
    )


def _nonblank(value: str | None) -> bool:
    return value is not None and bool(value.strip())
