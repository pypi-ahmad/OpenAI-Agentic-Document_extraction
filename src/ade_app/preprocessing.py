"""Conditional OpenCV preprocessing with reversible page coordinates.

Responsible for document image normalization (orientation detection, deskewing,
contrast adjustment) and maintaining invertible coordinate transforms (`PageTransform`)
between processed and original pages.
Must NOT extract text or perform layout segmentation.
Next: ade_app.layout for structural region detection on prepared pages.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from threading import Lock
from typing import Any, Literal

import pymupdf
from pydantic import Field, model_validator

from ade_app.constants import DEFAULT_DPI
from ade_app.inputs import DocumentInput
from ade_app.models import (
    Box,
    SemanticElement,
    SemanticFigure,
    SemanticTable,
    StrictModel,
)
from ade_app.raster import (
    RenderedPage,
    _encode_page,
    _open_image,
    _PdfPageLoadError,
    _rasterize_pdf_page,
)

_ORIENTATION_MODEL: Any | None = None
_ORIENTATION_DEVICE = "uninitialized"
_ORIENTATION_MODEL_LOCK = Lock()
_ORIENTATION_INFERENCE_LOCK = Lock()
logger = logging.getLogger(__name__)

# PaddleX otherwise probes every supported model host before consulting its local cache.
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


class _OrientationDetectionError(RuntimeError):
    pass


class _ImageDecodeError(ValueError):
    pass


class _ImageEncodeError(ValueError):
    pass


class PreprocessingOperation(StrictModel):
    kind: Literal["rotation", "deskew", "denoise", "contrast"]
    applied: bool
    reason: str
    value: float | None = None


class PageTransform(StrictModel):
    """Affine mapping from original pixels to prepared pixels and its inverse."""

    forward: list[list[float]] = Field(min_length=3, max_length=3)
    inverse: list[list[float]] = Field(min_length=3, max_length=3)
    operations: list[PreprocessingOperation]

    @model_validator(mode="after")
    def validate_matrices(self) -> PageTransform:
        if any(len(row) != 3 for matrix in (self.forward, self.inverse) for row in matrix):
            raise ValueError("page transforms must be 3x3 matrices")
        return self


class PagePreprocessingMetadata(StrictModel):
    source_page: int = Field(ge=1)
    requested_dpi: int | None = Field(default=None, ge=1)
    effective_dpi: float | None = Field(default=None, gt=0)
    original_width: int = Field(ge=1)
    original_height: int = Field(ge=1)
    cleaned_width: int = Field(ge=1)
    cleaned_height: int = Field(ge=1)
    rotation_angle: Literal[0, 90, 180, 270]
    rotation_confidence: float = Field(ge=0, le=1)
    deskew_angle: float


class PageIngestionError(StrictModel):
    source_page: int | None = Field(default=None, ge=1)
    stage: Literal[
        "source_open", "page_load", "render", "decode", "orientation", "preprocess", "encode"
    ]
    message: str
    cause_type: str


@dataclass(frozen=True, slots=True)
class PreparedPage:
    original: RenderedPage
    page: RenderedPage
    transform: PageTransform
    metadata: PagePreprocessingMetadata | None = None

    def box_to_original(self, box: Box) -> Box:
        """Map a prepared normalized box back onto the immutable original page."""

        cv2, np = _opencv()
        points = np.array(
            [
                [box.xmin * self.page.width, box.ymin * self.page.height],
                [box.xmax * self.page.width, box.ymin * self.page.height],
                [box.xmax * self.page.width, box.ymax * self.page.height],
                [box.xmin * self.page.width, box.ymax * self.page.height],
            ],
            dtype=np.float32,
        ).reshape(-1, 1, 2)
        inverse = np.asarray(self.transform.inverse, dtype=np.float64)
        mapped = cv2.perspectiveTransform(points, inverse).reshape(-1, 2)
        left, top = mapped.min(axis=0)
        right, bottom = mapped.max(axis=0)
        return Box(
            xmin=round(float(max(0, min(self.original.width, left))) / self.original.width, 5),
            ymin=round(float(max(0, min(self.original.height, top))) / self.original.height, 5),
            xmax=round(float(max(0, min(self.original.width, right))) / self.original.width, 5),
            ymax=round(float(max(0, min(self.original.height, bottom))) / self.original.height, 5),
        )

    def element_to_original(self, element: SemanticElement) -> SemanticElement:
        """Map every semantic box from the cleaned page to the source page."""

        transformed = element.model_copy(deep=True)
        boxes = [transformed.box]
        if isinstance(transformed, SemanticTable):
            boxes.extend(cell.box for cell in transformed.children)
        else:
            if isinstance(transformed, SemanticFigure):
                boxes.append(transformed.description.box)
            boxes.extend(line.box for line in transformed.lines)
        for box in boxes:
            original = self.box_to_original(box)
            box.xmin, box.ymin, box.xmax, box.ymax = (
                original.xmin,
                original.ymin,
                original.xmax,
                original.ymax,
            )
        return transformed


@dataclass(frozen=True, slots=True)
class IngestionResult:
    pages: tuple[PreparedPage, ...]
    errors: tuple[PageIngestionError, ...]

    @property
    def cleaned_pages(self) -> tuple[RenderedPage, ...]:
        return tuple(item.page for item in self.pages)

    @property
    def metadata(self) -> tuple[PagePreprocessingMetadata, ...]:
        return tuple(item.metadata for item in self.pages if item.metadata is not None)


def ingest_document(
    source: DocumentInput,
    pages: tuple[int, ...],
    dpi: int = DEFAULT_DPI,
) -> IngestionResult:
    """Render and clean selected pages while isolating page-level failures."""

    if dpi < 72 or dpi > 600:
        raise ValueError("dpi must be between 72 and 600")
    if not pages or len(pages) != len(set(pages)) or tuple(sorted(pages)) != pages:
        raise ValueError("selected pages must be strictly increasing and unique")

    prepared: list[PreparedPage] = []
    errors: list[PageIngestionError] = []
    if source.suffix != ".pdf":
        if pages != (1,):
            raise ValueError("image inputs contain exactly one page")
        try:
            image = _open_image(source.data, source.suffix)
        except (OSError, ValueError) as error:
            errors.append(_ingestion_error(source, 1, "decode", error))
            return IngestionResult((), tuple(errors))
        try:
            rendered = _encode_page(image, 1)
        except Exception as error:
            errors.append(_ingestion_error(source, 1, "encode", error))
            return IngestionResult((), tuple(errors))
        try:
            prepared.append(prepare_page(rendered))
        except Exception as error:
            errors.append(_ingestion_error(source, 1, _preprocessing_stage(error), error))
        return IngestionResult(tuple(prepared), tuple(errors))

    try:
        document = pymupdf.open(stream=source.data, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError) as error:
        return IngestionResult((), (_ingestion_error(source, None, "source_open", error),))

    with document:
        if document.needs_pass:
            error = ValueError("password-protected PDFs are not supported")
            return IngestionResult((), (_ingestion_error(source, None, "source_open", error),))
        if document.page_count < 1:
            error = ValueError("PDF contains no pages")
            return IngestionResult((), (_ingestion_error(source, None, "source_open", error),))
        for source_page in pages:
            try:
                rendered = _rasterize_pdf_page(document, source_page, dpi)
            except (ValueError, RuntimeError, pymupdf.FileDataError) as error:
                stage = (
                    "page_load"
                    if isinstance(error, _PdfPageLoadError) or "outside the PDF" in str(error)
                    else "render"
                )
                errors.append(_ingestion_error(source, source_page, stage, error))
                continue
            try:
                prepared.append(prepare_page(rendered))
            except Exception as error:
                errors.append(
                    _ingestion_error(source, source_page, _preprocessing_stage(error), error)
                )
    return IngestionResult(tuple(prepared), tuple(errors))


def prepare_page(page: RenderedPage) -> PreparedPage:
    """Apply only image transforms justified by measured page conditions."""

    cv2, np = _opencv()
    encoded = np.frombuffer(page.png_bytes, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        raise _ImageDecodeError("rendered page could not be decoded by OpenCV")

    try:
        rotation_angle, rotation_confidence = _predict_orientation(image)
    except Exception as error:
        rotation_angle, rotation_confidence = 0, 0.0
        logger.warning(
            "Orientation classification unavailable; continuing without coarse rotation",
            extra={
                "event": "orientation_fallback",
                "source_page": page.source_page,
                "status": "degraded",
                "error_code": type(error).__name__,
            },
        )
    image, coarse = _rotate_right_angle(image, rotation_angle, cv2, np)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    angle = _deskew_angle(gray, cv2, np)
    operations = [
        PreprocessingOperation(
            kind="rotation",
            applied=rotation_angle != 0,
            reason=(
                "PP-LCNet_x1_0_doc_ori"
                if rotation_confidence > 0
                else "orientation_classifier_unavailable"
            ),
            value=float(rotation_angle),
        )
    ]
    forward = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)
    output_height, output_width = image.shape[:2]
    if 0.25 <= abs(angle) <= 7.0:
        center = (output_width / 2.0, output_height / 2.0)
        forward = cv2.getRotationMatrix2D(center, angle, 1.0)
        image = cv2.warpAffine(
            image,
            forward,
            (output_width, output_height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        operations.append(
            PreprocessingOperation(
                kind="deskew", applied=True, reason="text_angle", value=round(angle, 3)
            )
        )
    else:
        operations.append(
            PreprocessingOperation(
                kind="deskew",
                applied=False,
                reason="angle_below_threshold",
                value=round(angle, 3),
            )
        )

    noise = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if noise > 1_800:
        image = cv2.fastNlMeansDenoisingColored(image, None, 5, 5, 7, 21)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        operations.append(
            PreprocessingOperation(
                kind="denoise",
                applied=True,
                reason="high_laplacian_variance",
                value=round(noise, 3),
            )
        )
    else:
        operations.append(
            PreprocessingOperation(
                kind="denoise",
                applied=False,
                reason="noise_within_threshold",
                value=round(noise, 3),
            )
        )

    contrast = float(gray.std())
    if contrast < 38:
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        lightness, channel_a, channel_b = cv2.split(lab)
        lightness = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lightness)
        image = cv2.cvtColor(cv2.merge((lightness, channel_a, channel_b)), cv2.COLOR_LAB2BGR)
        operations.append(
            PreprocessingOperation(
                kind="contrast",
                applied=True,
                reason="low_standard_deviation",
                value=round(contrast, 3),
            )
        )
    else:
        operations.append(
            PreprocessingOperation(
                kind="contrast",
                applied=False,
                reason="contrast_within_threshold",
                value=round(contrast, 3),
            )
        )

    ok, output = cv2.imencode(".png", image)
    if not ok:
        raise _ImageEncodeError("OpenCV could not encode the prepared page")
    forward_3x3 = np.vstack((forward, [0.0, 0.0, 1.0])) @ coarse
    inverse = np.linalg.inv(forward_3x3)
    cleaned_height, cleaned_width = image.shape[:2]
    return PreparedPage(
        original=page,
        page=RenderedPage(
            page.source_page,
            output.tobytes(),
            cleaned_width,
            cleaned_height,
            requested_dpi=page.requested_dpi,
            effective_dpi=page.effective_dpi,
        ),
        transform=PageTransform(
            forward=forward_3x3.tolist(), inverse=inverse.tolist(), operations=operations
        ),
        metadata=PagePreprocessingMetadata(
            source_page=page.source_page,
            requested_dpi=page.requested_dpi,
            effective_dpi=page.effective_dpi,
            original_width=page.width,
            original_height=page.height,
            cleaned_width=cleaned_width,
            cleaned_height=cleaned_height,
            rotation_angle=rotation_angle,
            rotation_confidence=rotation_confidence,
            deskew_angle=round(angle, 3),
        ),
    )


def _predict_orientation(image: Any) -> tuple[Literal[0, 90, 180, 270], float]:
    model = _orientation_model()
    with _ORIENTATION_INFERENCE_LOCK:
        try:
            predictions = list(model.predict(image, batch_size=1))
        except Exception as error:
            if _ORIENTATION_DEVICE == "cpu" or not _is_accelerator_error(error):
                raise
            model = _switch_orientation_to_cpu()
            predictions = list(model.predict(image, batch_size=1))
    if len(predictions) != 1:
        raise ValueError("orientation classifier returned no single result")
    payload = getattr(predictions[0], "json", predictions[0])
    if callable(payload):
        payload = payload()
    if isinstance(payload, dict) and isinstance(payload.get("res"), dict):
        payload = payload["res"]
    if not isinstance(payload, dict):
        raise ValueError("orientation classifier returned an unsupported result")
    labels = payload.get("label_names")
    scores = payload.get("scores")
    label = labels[0] if isinstance(labels, (list, tuple)) and labels else labels
    score = scores[0] if isinstance(scores, (list, tuple)) and scores else scores
    angle = int(label)
    if angle not in {0, 90, 180, 270} or score is None:
        raise ValueError("orientation classifier returned an invalid angle or score")
    return angle, max(0.0, min(1.0, float(score)))


def _orientation_model() -> Any:
    global _ORIENTATION_DEVICE, _ORIENTATION_MODEL
    if _ORIENTATION_MODEL is None:
        with _ORIENTATION_MODEL_LOCK:
            if _ORIENTATION_MODEL is None:
                try:
                    import paddle
                    from paddleocr import DocImgOrientationClassification
                except ImportError as error:
                    raise RuntimeError(
                        "Paddle document orientation classifier is unavailable"
                    ) from error
                device = (
                    "gpu:0"
                    if paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count()
                    else "cpu"
                )
                try:
                    _ORIENTATION_MODEL = DocImgOrientationClassification(
                        model_name="PP-LCNet_x1_0_doc_ori", device=device
                    )
                    _ORIENTATION_DEVICE = device
                except Exception as error:
                    if device == "cpu" or not _is_accelerator_error(error):
                        raise
                    _ORIENTATION_MODEL = DocImgOrientationClassification(
                        model_name="PP-LCNet_x1_0_doc_ori", device="cpu"
                    )
                    _ORIENTATION_DEVICE = "cpu"
    return _ORIENTATION_MODEL


def _rotate_right_angle(
    image: Any, angle: Literal[0, 90, 180, 270], cv2: Any, np: Any
) -> tuple[Any, Any]:
    height, width = image.shape[:2]
    if angle == 90:
        matrix = np.array([[0, 1, 0], [-1, 0, width - 1], [0, 0, 1]], dtype=np.float64)
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE), matrix
    if angle == 180:
        matrix = np.array([[-1, 0, width - 1], [0, -1, height - 1], [0, 0, 1]], dtype=np.float64)
        return cv2.rotate(image, cv2.ROTATE_180), matrix
    if angle == 270:
        matrix = np.array([[0, -1, height - 1], [1, 0, 0], [0, 0, 1]], dtype=np.float64)
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE), matrix
    return image, np.eye(3, dtype=np.float64)


def _is_accelerator_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(token in message for token in ("cuda", "cudnn", "gpu", "out of memory"))


def _switch_orientation_to_cpu() -> Any:
    global _ORIENTATION_DEVICE, _ORIENTATION_MODEL
    with _ORIENTATION_MODEL_LOCK:
        if _ORIENTATION_DEVICE != "cpu":
            try:
                from paddleocr import DocImgOrientationClassification
            except ImportError as error:
                raise RuntimeError(
                    "Paddle document orientation classifier is unavailable"
                ) from error
            _ORIENTATION_MODEL = DocImgOrientationClassification(
                model_name="PP-LCNet_x1_0_doc_ori", device="cpu"
            )
            _ORIENTATION_DEVICE = "cpu"
    return _ORIENTATION_MODEL


def _ingestion_error(
    source: DocumentInput,
    source_page: int | None,
    stage: Literal[
        "source_open", "page_load", "render", "decode", "orientation", "preprocess", "encode"
    ],
    error: Exception,
) -> PageIngestionError:
    location = source.filename if source_page is None else f"{source.filename}, page {source_page}"
    if "password-protected" in str(error):
        message = f"{location}: password-protected PDFs are not supported"
    elif "outside the PDF" in str(error):
        message = f"{location}: requested page is outside the PDF"
    else:
        message = f"{location}: {stage.replace('_', ' ')} failed"
    return PageIngestionError(
        source_page=source_page,
        stage=stage,
        message=message,
        cause_type=type(error).__name__,
    )


def _preprocessing_stage(
    error: Exception,
) -> Literal["decode", "orientation", "preprocess", "encode"]:
    if isinstance(error, _ImageDecodeError):
        return "decode"
    if isinstance(error, _OrientationDetectionError):
        return "orientation"
    if isinstance(error, _ImageEncodeError):
        return "encode"
    return "preprocess"


def _deskew_angle(gray: Any, cv2: Any, np: Any) -> float:
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    threshold = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    points = np.column_stack(np.where(threshold > 0))
    if len(points) < 100:
        return 0.0
    angle = float(cv2.minAreaRect(points[:, ::-1].astype(np.float32))[-1])
    if angle < -45:
        angle += 90
    elif angle > 45:
        angle -= 90
    return -angle


def _opencv() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as error:
        raise RuntimeError("OpenCV preprocessing dependencies are unavailable") from error
    return cv2, np
