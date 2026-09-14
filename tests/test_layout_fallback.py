from ade_app import layout
from ade_app.preprocessing import PageTransform, PreparedPage
from ade_app.raster import RenderedPage


def _prepared() -> PreparedPage:
    page = RenderedPage(1, b"png", 100, 100)
    return PreparedPage(
        original=page,
        page=page,
        transform=PageTransform(
            forward=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            inverse=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            operations=[],
        ),
    )


def test_accelerator_error_classifier_is_narrow() -> None:
    assert layout._is_accelerator_error(RuntimeError("CUDA out of memory"))
    assert not layout._is_accelerator_error(ValueError("malformed response"))


def test_missing_model_returns_structured_unavailable_result(monkeypatch) -> None:
    prepared = _prepared()
    issue = layout.LayoutIssue(
        code="model_unavailable",
        stage="model_init",
        message="PP-StructureV3 model files are unavailable",
        cause_type="FileNotFoundError",
        attempted_devices=("cpu",),
    )
    monkeypatch.setattr(layout, "_geometry_proposals", lambda value: [])
    monkeypatch.setattr(
        layout,
        "_predict",
        lambda value: (_ for _ in ()).throw(layout._ModelUnavailable(issue)),
    )

    result = layout.PPStructureAnalyzer().analyze(prepared)

    assert result.status == "unavailable"
    assert result.regions == []
    assert result.issues == [issue]


def test_gpu_inference_retries_once_on_cpu(monkeypatch) -> None:
    class GpuModel:
        def predict(self, *, input):
            raise RuntimeError("CUDA out of memory")

    class CpuModel:
        def predict(self, *, input):
            return [{"res": {"parsing_res_list": []}}]

    monkeypatch.setattr(layout, "_model", lambda: (GpuModel(), "gpu:0", ("gpu:0",)))
    monkeypatch.setattr(layout, "_switch_to_cpu", lambda: CpuModel())

    predictions, device, attempted, issue = layout._predict(_prepared())

    assert predictions == [{"res": {"parsing_res_list": []}}]
    assert device == "cpu"
    assert attempted == ("gpu:0", "cpu")
    assert issue is not None
    assert issue.code == "accelerator_failed"
    assert issue.recovered is True
