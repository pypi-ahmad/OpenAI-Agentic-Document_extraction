import io

from PIL import Image

from ade_app.inputs import DocumentInput
from ade_app.models import Box
from ade_app.preprocessing import PageTransform, PreparedPage, ingest_document, prepare_page
from ade_app.raster import RenderedPage


def test_identity_transform_preserves_normalized_box() -> None:
    page = RenderedPage(1, b"unused", 100, 200)
    prepared = PreparedPage(
        original=page,
        page=page,
        transform=PageTransform(
            forward=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            inverse=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            operations=[],
        ),
    )

    assert prepared.box_to_original(Box(xmin=0.1, ymin=0.2, xmax=0.5, ymax=0.8)) == Box(
        xmin=0.1, ymin=0.2, xmax=0.5, ymax=0.8
    )


def test_prepare_page_applies_classifier_rotation(monkeypatch) -> None:
    output = io.BytesIO()
    Image.new("RGB", (80, 40), "white").save(output, format="PNG")
    page = RenderedPage(1, output.getvalue(), 80, 40, requested_dpi=300, effective_dpi=300)
    monkeypatch.setattr("ade_app.preprocessing._predict_orientation", lambda image: (90, 0.99))

    prepared = prepare_page(page)

    assert (prepared.page.width, prepared.page.height) == (40, 80)
    assert prepared.metadata is not None
    assert prepared.metadata.rotation_angle == 90
    assert prepared.metadata.rotation_confidence == 0.99
    assert prepared.transform.operations[0].kind == "rotation"


def test_ingestion_keeps_pages_after_page_render_failure(monkeypatch) -> None:
    class Document:
        needs_pass = False
        page_count = 3

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    source = DocumentInput(filename="sample.pdf", data=b"%PDF-stub")
    page = RenderedPage(1, b"png", 10, 10, requested_dpi=300, effective_dpi=300)
    identity = PageTransform(
        forward=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        inverse=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        operations=[],
    )
    monkeypatch.setattr("ade_app.preprocessing.pymupdf.open", lambda **kwargs: Document())

    def render(document, source_page, dpi):
        if source_page == 2:
            raise RuntimeError("render failed")
        return page.__class__(source_page, page.png_bytes, 10, 10, dpi, dpi)

    monkeypatch.setattr("ade_app.preprocessing._rasterize_pdf_page", render)
    monkeypatch.setattr(
        "ade_app.preprocessing.prepare_page",
        lambda rendered: PreparedPage(rendered, rendered, identity),
    )

    result = ingest_document(source, (1, 2, 3))

    assert [item.page.source_page for item in result.pages] == [1, 3]
    assert [(error.source_page, error.stage) for error in result.errors] == [(2, "render")]
