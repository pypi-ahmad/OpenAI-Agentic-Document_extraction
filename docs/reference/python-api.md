# Python API reference

The Streamlit app and console scripts are supported user entry points. Python modules are internal
application APIs and may evolve; these are the principal integration surfaces.

Minimal local input inspection requires no OpenAI request:

```python
from pathlib import Path

from ade_app.inputs import DocumentInput
from ade_app.raster import get_page_count

path = Path("scan.pdf")
source = DocumentInput(filename=path.name, data=path.read_bytes())
print(get_page_count(source))
```

## Inputs and rasterization

### `DocumentInput(filename: str, data: bytes)`

Validated in-memory source. The filename must be a plain basename with a supported suffix, and
data must be nonempty. `suffix` and `stem` expose normalized file information.

### `parse_page_range(specification: str, page_count: int) -> tuple[int, ...]`

Parses one-based inclusive specifications such as `1,3-5`. Empty input selects every page. It
returns sorted, deduplicated pages and raises `ValueError` for malformed or out-of-bounds input.

### `get_page_count(source: DocumentInput) -> int`

Returns the PDF page count or 1 for an image. Invalid input raises `ValueError`.

### `rasterize_document(source, pages, dpi=200) -> Iterator[RenderedPage]`

Yields requested pages as PNG-backed values with dimensions and source page numbers. DPI is
reduced when necessary to remain within image limits. Callers that need repeated iteration must
materialize the iterator themselves.

## Extraction

### `PageExtractor`

Protocol accepted by `extract_document` and `extract_documents`. Its `extract` method receives a
`RenderedPage` plus the parse job ID and source page count, and returns a validated
`PageResponse`. `OpenAIPageExtractor` is the production implementation; tests can supply a local
fake without making a network request.

### `BatchDocument(item_id, source, pages)`

Input record for `extract_documents`. `item_id` must be unique within the batch, `source` is a
`DocumentInput`, and `pages` is a strictly increasing tuple of one-based source pages. Batch
validation also enforces aggregate file, byte, page, and raster-pixel limits.

### `OpenAIPageExtractor`

Production extractor around the Responses parser. It performs structured semantic extraction,
validation, quality assessment, and targeted repair while capturing request IDs and usage.
Construct its parser with `build_responses_parser(resolve_api_key())`; construction is local, but
calling extraction sends selected page images to OpenAI and requires the calibrated quality
profile.

### `extract_document(source, pages, extractor, ...) -> ExtractionRun`

Runs rasterization, extraction, strict assembly, deterministic rendering, annotation, and
packaging. It preserves successful pages after page-level failures. Bad configuration raises
`ValueError`; unrecoverable errors use `DocumentExtractionError` with partial usage/page records.

### `extract_documents(documents, extractor, ...) -> BatchExtractionRun`

Runs 1–20 `BatchDocument` values with bounded file concurrency. It enforces file, byte, and page
limits and returns results in input order, including document-level failure records.

## Models and validation

### `GroundTruthDocument`

Strict final artifact model. `model_validate_json(text)` validates JSON and `model_dump_json()`
serializes it. Cross-field validators enforce page order, ranges, IDs, grounding containment,
failed-page consistency, and Markdown length.

### `SemanticPageExtraction`

Model-facing structure containing semantic text, figures, tables, cells, styles, checkboxes,
source kinds, and normalized boxes. It excludes presentation formatting and final offsets.

### `TokenUsage` and `calculate_cost(usage, model)`

`TokenUsage` rejects negative or inconsistent counts. `calculate_cost` returns an exact `Decimal`
using configured rates and raises `ValueError` for unsupported models.

## Output helpers

### `build_annotated_pdf(pages, artifact)`

Overlays trustworthy element/cell boxes on rendered pages. It returns PDF bytes and annotation
limitations. Zero-area or unavailable boxes are omitted and explained.

### `build_output_bundle(...)` and `build_batch_output_bundle(...)`

Create in-memory ZIP files. Duplicate batch folders raise `ValueError`.

## Command entry points

- `ade-profile` → `ade_app.profile:main`
- `ade-evaluate` → `ade_app.evaluation:main`
- `ade-calibrate-quality` → `ade_app.calibration:main`

Use each command's `--help` output as the definitive argument reference.
