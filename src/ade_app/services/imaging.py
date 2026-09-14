"""Imaging service boundary.

Responsible for re-exporting document rasterization, preprocessing, and page transformation types.
Must NOT execute image processing directly; logic lives in ade_app.preprocessing and ade_app.raster.
Next: ade_app.preprocessing for image cleanup, or ade_app.raster for byte decoding.
"""

from ade_app.preprocessing import (
    IngestionResult,
    PageIngestionError,
    PagePreprocessingMetadata,
    PageTransform,
    PreparedPage,
    ingest_document,
    prepare_page,
)

__all__ = [
    "IngestionResult",
    "PageIngestionError",
    "PagePreprocessingMetadata",
    "PageTransform",
    "PreparedPage",
    "ingest_document",
    "prepare_page",
]
