import json
import zipfile
from io import BytesIO

from ade_app.artifacts import build_artifacts, estimated_cost
from ade_app.models import Block, Box, DocumentResult, PageResult, Usage
from ade_app.raster import RenderedPage


def test_exports_derive_from_one_result_and_sanitize_html(png_bytes: bytes) -> None:
    block = Block(
        id="figure-0",
        type="figure",
        markdown="<script>x</script>Visible",
        box=Box(xmin=0, ymin=0, xmax=1, ymax=1),
        asset="assets/page-1-figure-0.png",
    )
    page = PageResult(page=1, width=200, height=100, blocks=[block])
    document = DocumentResult(
        source_filename="sample.png",
        pages=[page],
        markdown="<script>x</script>Visible\n\n![figure](assets/page-1-figure-0.png)\n",
    )
    artifacts = build_artifacts(
        document, {block.asset: png_bytes}, [RenderedPage(1, png_bytes, 200, 100)]
    )
    assert "<script" not in artifacts.html
    assert "data:image/png;base64" in artifacts.html
    assert json.loads(artifacts.json_text)["model"] == "gpt-6-sol"
    with zipfile.ZipFile(BytesIO(artifacts.zip_bytes)) as archive:
        assert block.asset in archive.namelist()
        assert artifacts.pdf_name in archive.namelist()


def test_cost_is_unavailable_when_usage_is_incomplete() -> None:
    document = DocumentResult(
        source_filename="sample.png",
        pages=[],
        markdown="",
        usage=Usage(input_tokens=10, calls=2, complete=False),
    )

    assert estimated_cost(document) is None
