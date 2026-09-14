"""Output artifact service boundary.

Responsible for re-exporting annotated PDF and output-bundle builders from ade_app.outputs.
Must NOT generate PDFs or bundle archives directly; logic lives in ade_app.outputs.
Next: ade_app.outputs for full artifact generation logic.
"""

from ade_app.outputs import build_annotated_pdf, build_batch_output_bundle, build_output_bundle

__all__ = ["build_annotated_pdf", "build_batch_output_bundle", "build_output_bundle"]
