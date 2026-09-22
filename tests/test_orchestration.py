from decimal import Decimal

from ade_app.contracts import ExtractionRun
from ade_app.cost import TokenUsage
from ade_app.inputs import DocumentInput
from ade_app.orchestration import (
    DocumentWorkflowState,
    ExtractionStageResult,
    FieldRetryPlan,
    IngestionStageResult,
    LayoutStageResult,
    ValidationStageResult,
    WorkflowConfig,
    WorkflowRequest,
    run_document_workflow,
    run_page_workflow,
)
from ade_app.raster import RenderedPage


def test_page_workflow_fans_out_and_restores_source_order() -> None:
    pages = (RenderedPage(2, b"two", 1, 1), RenderedPage(1, b"one", 1, 1))
    results = run_page_workflow(pages, lambda page: ("ok", page, "standard"))
    assert [item[1].source_page for item in results] == [1, 2]


def test_page_workflow_accepts_empty_page_selection() -> None:
    assert run_page_workflow((), lambda page: page) == []


class RecordingOperations:
    def __init__(self) -> None:
        self.stages: list[str] = []
        self.route_calls = 0
        self.extraction = ExtractionStageResult(
            outcomes=(),
            page_records=(),
            raw_fields=(),
            usage=TokenUsage(),
            cost_usd=Decimal(0),
            service_tier="standard",
            model_version="gpt-6-sol",
            peer_evidence_count=0,
            duration_ms=0,
        )

    def ingest_preprocess(self, request):
        self.stages.append("ingest_preprocess")
        return IngestionStageResult(1, (), (), ())

    def layout_analysis(self, ingestion):
        self.stages.append("layout_analysis")
        return LayoutStageResult(())

    def route_and_extract(self, layout, ingestion, previous, retry_plan):
        self.stages.append("route_and_extract")
        self.route_calls += 1
        return self.extraction

    def validate_and_link(self, extraction):
        self.stages.append("validate_and_link")
        retry = FieldRetryPlan(mandatory_region_ids=("region-1",))
        return ValidationStageResult((), {}, 0, 0, retry if self.route_calls == 1 else None)

    def generate_outputs(self, ingestion, extraction, validation):
        self.stages.append("generate_outputs")
        return object.__new__(ExtractionRun)


def test_document_workflow_runs_all_stages_and_sol_conditional_edge() -> None:
    operations = RecordingOperations()
    request = WorkflowRequest(DocumentInput("sample.png", b"image"), (1,), 300)
    result = run_document_workflow(
        DocumentWorkflowState(operations=operations, request=request, config=WorkflowConfig())
    )
    assert isinstance(result.output, ExtractionRun)
    assert operations.stages == [
        "ingest_preprocess",
        "layout_analysis",
        "route_and_extract",
        "validate_and_link",
        "route_and_extract",
        "validate_and_link",
        "generate_outputs",
    ]
