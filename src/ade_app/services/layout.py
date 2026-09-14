"""Layout-analysis service boundary.

Responsible for re-exporting the PP-StructureV3 layout analyzer, routing decisions,
and region types.
Must NOT execute model inference directly; logic lives in ade_app.layout.
Next: ade_app.layout for structural region detection.
"""

from ade_app.layout import (
    GeometryProposal,
    LayoutAnalysis,
    LayoutIssue,
    LayoutRegion,
    PPStructureAnalyzer,
    RegionCrop,
    RouteDecision,
    decide_routes,
)

__all__ = [
    "GeometryProposal",
    "LayoutAnalysis",
    "LayoutIssue",
    "LayoutRegion",
    "PPStructureAnalyzer",
    "RegionCrop",
    "RouteDecision",
    "decide_routes",
]
