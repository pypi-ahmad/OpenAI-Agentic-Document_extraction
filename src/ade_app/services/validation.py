"""Post-processing and strict validation service boundary.

Responsible for re-exporting document artifact builders and provenance manifest validators.
Must NOT execute validation logic directly; logic lives in ade_app.fields and ade_app.provenance.
Next: ade_app.fields for field linking, or ade_app.provenance for manifest schemas.
"""

from ade_app.fields import build_v2_artifact
from ade_app.provenance import validate_document_manifest

__all__ = ["build_v2_artifact", "validate_document_manifest"]
