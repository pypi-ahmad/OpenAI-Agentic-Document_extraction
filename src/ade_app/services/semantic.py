"""Tiered semantic extraction service boundary.

Responsible for re-exporting hybrid local and OpenAI model page extractors.
Must NOT execute extraction logic directly; logic lives in ade_app.hybrid and ade_app.openai_client.
Next: ade_app.hybrid for layout-guided fallback extraction.
"""

from ade_app.hybrid import HybridPageExtractor
from ade_app.openai_client import OpenAIPageExtractor

__all__ = ["HybridPageExtractor", "OpenAIPageExtractor"]
