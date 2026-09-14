"""Public re-export of the strict v2 extraction-document schema.

Responsible for exposing ExtractionDocumentV2 at a stable public import path.
Must NOT define new model fields or business logic; models live in ade_app.models.
Next: ade_app.models for core model definitions and schema validators.
"""

from ade_app.models import ExtractionDocumentV2

__all__ = ["ExtractionDocumentV2"]
