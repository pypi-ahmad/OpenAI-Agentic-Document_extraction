"""Top-level package root for ade_app.

Responsible for defining package metadata and re-exporting the primary MODEL_ID cascade identifier.
Must NOT import heavy deep-learning frameworks (Paddle, PyMuPDF, Torch) at top level.
Next: ade_app.pipeline for extraction workflows, or ade_app.cli for command-line entry.
"""

from ade_app.constants import MODEL_ID

__all__ = ["MODEL_ID"]
