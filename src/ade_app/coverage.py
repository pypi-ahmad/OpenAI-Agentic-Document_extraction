"""Bounded image coverage checks for unextracted foreground content.

Responsible for detecting text-sized foreground connected components outside
supplied bounding boxes.
Must NOT synthesize or extract text; returned bounding boxes represent omission evidence only.
Next: ade_app.hybrid where coverage findings are integrated into audit checks.
"""

from __future__ import annotations

from collections.abc import Iterable

from ade_app.models import Box
from ade_app.raster import RenderedPage


def uncovered_foreground(page: RenderedPage, boxes: Iterable[Box]) -> tuple[Box, ...]:
    """Find text-sized foreground outside supplied boxes at at most 1200px resolution."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(page.png_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError("coverage image could not be decoded")
    scale = min(1.0, 1200 / max(image.shape))
    image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    height, width = image.shape
    _, mask = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    # Ignore scan borders and long form ruling lines, retaining adjacent handwriting.
    edge = max(1, round(min(height, width) * 0.005))
    mask[:edge] = mask[-edge:] = 0
    mask[:, :edge] = mask[:, -edge:] = 0
    for size in ((max(30, width // 8), 1), (1, max(30, height // 8))):
        lines = cv2.morphologyEx(
            mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, size)
        )
        mask = cv2.subtract(mask, lines)
    for box in boxes:
        x0, y0 = max(0, int(box.xmin * width) - 2), max(0, int(box.ymin * height) - 2)
        x1, y1 = min(width, int(box.xmax * width) + 3), min(height, int(box.ymax * height) + 3)
        mask[y0:y1, x0:x1] = 0
    grouped = cv2.dilate(mask, np.ones((3, 7), dtype=np.uint8))
    contours, _ = cv2.findContours(grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < 6 or h < 4 or cv2.countNonZero(mask[y : y + h, x : x + w]) < 12:
            continue
        found.append(
            Box(xmin=x / width, ymin=y / height, xmax=(x + w) / width, ymax=(y + h) / height)
        )
    return tuple(sorted(found, key=lambda box: (box.ymin, box.xmin))[:64])
