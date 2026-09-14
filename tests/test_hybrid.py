from ade_app.cost import TokenUsage
from ade_app.hybrid import HybridPageExtractor
from ade_app.layout import LayoutAnalysis, LayoutRegion, PPStructureAnalyzer
from ade_app.models import Box
from ade_app.openai_client import OpenAIPageExtractor
from ade_app.preprocessing import PageTransform, PreparedPage
from ade_app.raster import RenderedPage


class Analyzer(PPStructureAnalyzer):
    def analyze(self, prepared: PreparedPage) -> LayoutAnalysis:
        return LayoutAnalysis(
            source_page=prepared.page.source_page,
            regions=[
                LayoutRegion(
                    region_id="p1-r0",
                    kind="text",
                    confidence=0.99,
                    box=Box(xmin=0, ymin=0, xmax=1, ymax=1),
                    route="local_text",
                    text="Member ID: A123",
                )
            ],
            proposals=[],
        )


class LlmMustNotRun(OpenAIPageExtractor):
    def __init__(self):
        super().__init__(object(), profile_path=None)

    def extract_primary(self, *args, **kwargs):
        raise AssertionError("calibrated local OCR must not call Terra")


def test_calibrated_printed_page_builds_zero_cost_primary(monkeypatch) -> None:
    page = RenderedPage(1, b"png", 100, 100)
    monkeypatch.setattr(
        "ade_app.hybrid.prepare_page",
        lambda value: PreparedPage(
            original=value,
            page=value,
            transform=PageTransform(
                forward=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                inverse=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                operations=[],
            ),
        ),
    )
    extractor = HybridPageExtractor(
        LlmMustNotRun(), analyzer=Analyzer(), calibrated_routes={"local_text"}
    )

    primary = extractor.extract_primary(page, job_id="job", page_count=1)

    assert primary.usage == TokenUsage()
    assert primary.attempts == 0
    assert primary.source_model == "PP-StructureV3"


def test_graph_layout_is_consumed_without_reanalysis() -> None:
    page = RenderedPage(1, b"png", 100, 100)
    prepared = PreparedPage(
        original=page,
        page=page,
        transform=PageTransform(
            forward=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            inverse=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            operations=[],
        ),
    )
    analysis = Analyzer().analyze(prepared)

    class AnalyzerMustNotRun(PPStructureAnalyzer):
        def analyze(self, prepared):
            raise AssertionError("route_and_extract must not perform layout analysis")

    extractor = HybridPageExtractor(
        LlmMustNotRun(), analyzer=AnalyzerMustNotRun(), calibrated_routes={"local_text"}
    )

    primary = extractor.extract_primary_from_layout(
        page, prepared, analysis, job_id="job", page_count=1
    )

    assert primary.source_model == "PP-StructureV3"
