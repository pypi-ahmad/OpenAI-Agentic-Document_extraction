# Technical details

This document outlines the software stack, core design invariants, error handling strategies, and persistence mechanisms implemented in the codebase.

## Software stack

The components of the stack and their specific roles:

### Core runtime

- **Python 3.13** (`>=3.13.15,<3.14`): Target runtime version defined in `pyproject.toml` and `.python-version`.
- **uv** (`uv_build>=0.12.7,<0.13.0`): Package management, lockfile reproducibility (`uv.lock`), and build backend.

### Document processing and computer vision

- **PyMuPDF** (`pymupdf>=1.28.2`): In-memory PDF document rasterization to PNG bytes at calibrated DPI (`ade_app.raster`) and drawing vector bounding box overlays on annotated review PDFs (`ade_app.outputs`).
- **Pillow** (`pillow>=12.3.0`): Image decoding and format normalization for PNG, JPEG, TIFF, WEBP, and BMP formats (`ade_app.raster`).
- **OpenCV** (`opencv-contrib-python==5.0.0.93`): Image preprocessing in `ade_app.preprocessing` (deskewing via minAreaRect/Hough lines, orientation detection, contrast normalization) and form geometry proposal detection in `ade_app.layout`.
- **PP-StructureV3** (`paddleocr==3.7.0`, `paddlex[ocr]==3.7.2`, `paddlepaddle` / `paddlepaddle-gpu`): Local layout analysis in `ade_app.layout` to identify text blocks, tables, and form fields.

### Orchestration and data models

- **LangGraph** (`langgraph>=1.0.8,<2`): In-memory directed acyclic state graphs in `ade_app.orchestration` controlling pipeline stages (`ingest_preprocess`, `layout_analysis`, `route_and_extract`, `validate_and_link`, `generate_outputs`) and concurrent page fan-out.
- **Pydantic** (`pydantic>=2.13.5`): Schema validation and data modeling. `StrictModel` enforces `extra="forbid"` and `strict=True` to reject automatic type coercion and unregistered attributes.
- **OpenAI Python SDK** (`openai>=3.6.0`): Direct integration with the OpenAI Responses API in `ade_app.openai_client` using Pydantic structured output models.

### User interface

- **Streamlit** (`streamlit[pdf]>=1.62.0`): Web-based operator interface in `streamlit_app.py` for batch uploads, page selection, live progress monitoring, document previews, and artifact downloads.

### Tooling and verification

- **Pytest** (`pytest>=9.1.1`, `pytest-cov>=7.1.0`): Automated test suite execution and test coverage tracking.
- **Ruff** (`ruff>=0.16.5`): Linting and code formatting adhering to a 100-character line length, Python 3.13 target, and LF line endings.
- **ty** (`ty>=0.0.76`): Static type checking.

## System invariants

The codebase enforces the following architectural invariants:

### Reversible coordinate spaces

All element bounding boxes are expressed as normalized coordinates in the range `[0, 1]` with at most 5 decimal places:

$$\text{Box} = \{ x_{\min}, y_{\min}, x_{\max}, y_{\max} \} \quad \text{where} \quad 0 \le x_{\min} \le x_{\max} \le 1, \; 0 \le y_{\min} \le y_{\max} \le 1$$

Whenever preprocessing modifies the raw page (e.g., rotation, deskewing, margin cropping), a `PageTransform` record tracks the mathematical operations to allow bidirectional coordinate mapping between the processed model input and the original document page.

### Strict artifact validation

- **Version 3 schema (`ExtractionDocumentV3`)**: Non-observed values (`blank`, `illegible`, `ambiguous`, `conflicting`, `unverified`) must be represented as `null`. Only `observed` values can be populated strings or booleans.
- **Evidence grounding**: Accepted fields require verified evidence confirming model agreement or calibrated confidence. Any field failing validation checks cannot be accepted.
- **Manifest v9**: Output bundles must contain a cryptographic manifest detailing SHA-256 digests of all produced files, exact token usage, estimated cost, and review requirements.

### Concurrency and resource bounds

- **API concurrency**: A global threading semaphore in `ade_app.openai_client` restricts active OpenAI Responses calls to 4 (`MAX_CONCURRENT_RESPONSES = 4`), preventing rate limit exhaustion regardless of batch size.
- **Batch limits**: Multi-document processing in `ade_app.batch` enforces caps of at most 20 files, 500 MB total payload, 100 total pages, 4 concurrent documents, and 2 page workers per document.
- **Raster budget**: Single-page rendering is limited to 20,000,000 pixels (`MAX_RASTER_PIXELS`). Whole batches are capped at 100,000,000 pixels (`MAX_BATCH_RASTER_PIXELS`). When exceeded, rendering scales down effective DPI automatically.

### Thread serialization for Paddle

The underlying Paddle layout analysis engine is not thread-safe for concurrent inference. A dedicated process-wide threading lock (`_INFERENCE_LOCK` in `ade_app.layout`) serializes all inference calls across threads.

## Error handling

The system employs fail-closed error handling and fault isolation:

- **Page-level fault isolation**: If an individual page fails during ingestion, layout, or extraction, the failure is recorded in `PageRunRecord.failure_reason`. The pipeline continues processing remaining pages and documents.
- **Fail-closed review status**: If any page encounters a failure or any field remains unresolved, the run status is marked `needs_review` and the manifest review state is set to `required_unresolved` or `failed`.
- **Permanent layout failure latching**:
  - `_MODEL_FAILURE`: If layout model initialization permanently fails, subsequent requests bypass model loading and report `LayoutIssue`.
  - `_CPU_LATCHED`: If an accelerator error occurs on GPU and `allow_cpu_fallback` is enabled, the engine permanently latches to CPU for the remainder of the process.
- **Transport retries**: OpenAI API network errors are retried up to `transport_max_attempts` (default: 3) with exponential backoff and randomized jitter.

## Persistence paths

- **Memory-first execution**: Document contents, page rasters, intermediate extraction states, and LangGraph workflow nodes are retained in memory during execution. No intermediate state is written to temporary scratch files on disk during standard processing.
- **CLI file output**: `ade-extract` writes completed artifact bundles atomically using temporary files replaced into the destination directory (`--output-dir`).
- **Streamlit downloads**: Output files generated in the web UI are stored in Streamlit session memory and written to the client's filesystem only when the user triggers an explicit download action.
- **Evaluation outputs**: `ade-evaluate` writes execution traces, JSON reports, Markdown summaries, and ZIP archives directly under `evaluation/runs/`.
