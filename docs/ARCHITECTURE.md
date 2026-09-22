# Architecture

This reference covers ADE request/data flow, primary state types, and external system boundaries.

## System flow

The extraction pipeline uses an in-memory LangGraph workflow. Every request uses `gpt-6-sol`
with medium reasoning. Extraction always runs, and default `baseline` routing uses full pages.
Explicit `local_first` and `selective` configurations retain regional routing. Verification and
repair default off, can be enabled independently, and use the same model. Primary drafts are
exported separately from the fail-closed v3 artifact.

```mermaid
flowchart TD
    A["Input Document (PDF/Image)"] --> B["Ingest & Preprocess<br/>(ade_app.services.imaging / ade_app.preprocessing)"]
    B --> C["Layout Analysis<br/>(ade_app.services.layout / ade_app.layout)"]
    C --> D["Route & Extract<br/>(ade_app.hybrid / ade_app.orchestration.nodes)"]
    D --> E{"Quality Gate & Agreement<br/>(ade_app.quality / ade_app.consensus)"}
    D --> K["Unverified primary draft"]
    E -- "Evidence or unresolved status" --> F["Validate & Link<br/>(ade_app.fields / ade_app.services.validation)"]
    E -- "Repair enabled / flagged" --> G["GPT-6 Sol Field Resolution<br/>(ade_app.openai_client)"]
    G --> F
    F --> H{"Validation Status"}
    H -- "Repair enabled / bounded retry" --> G
    H -- "Resolved or Flagged" --> I["Generate Outputs<br/>(ade_app.rendering / ade_app.outputs / ade_app.provenance)"]
    K --> J["Output Bundle<br/>(Drafts, Markdown, JSON v3, Confidence, Annotated PDF, Manifest, ZIP)"]
    I --> J
```

### Execution stages

1. **Ingest and preprocess** (`ade_app.preprocessing.ingest_document`, `ade_app.raster`):
   Loads PDF or image bytes into memory. PyMuPDF renders PDF pages to PNG at the target DPI (default 300 DPI, capped by raster pixel limits). OpenCV assesses page rotation, deskew angle, and contrast, producing a `PreparedPage` and reversible `PageTransform`.
2. **Layout analysis** (`ade_app.layout.PPStructureAnalyzer`):
   Runs PP-StructureV3 on the prepared page to identify text, table, and form regions, producing `LayoutAnalysis` with bounding boxes. In case of accelerator failure, it falls back to CPU if permitted by configuration.
3. **Route and extract** (`ade_app.hybrid.HybridPageExtractor`, `ade_app.openai_client`):
   Dispatches page images concurrently using `ade_app.orchestration.run_page_workflow`. Sends primary extraction requests to OpenAI `gpt-6-sol` using structured output (`src/ade_app/prompts/page_extraction.md`).
4. **Quality verification and consensus** (`ade_app.quality`, `ade_app.consensus`):
   When verification is enabled, unresolved segments are cropped and sent to `gpt-6-sol` (`src/ade_app/prompts/segment_consensus.md`) for an independent read.
5. **Dispute resolution and repair** (`ade_app.openai_client.OpenAIPageExtractor`):
   When repair is enabled, disagreements or flagged fields are reread by `gpt-6-sol` for targeted repair.
6. **Validate and link** (`ade_app.fields.link_and_validate_fields`):
   Links extracted fields and evidence and evaluates validation checks before v3 assembly. If repair is enabled, flagged fields require escalation, and retries remain, the graph loops back via `field_retry`.
7. **Generate outputs** (`ade_app.rendering`, `ade_app.outputs`, `ade_app.provenance`):
   Renders deterministic Markdown with `<!-- PAGE BREAK -->` dividers, serializes schema-validated v3 JSON, generates a confidence report, creates an annotated PDF with color-coded bounding boxes and review sidebar, calculates SHA-256 digests, and packages artifacts into a ZIP archive with a v10 manifest.

## Main types and state

These are the main data structures.

| Type | Module | Purpose |
| --- | --- | --- |
| `DocumentWorkflowState` | `ade_app.orchestration.state` | State object for the LangGraph document extraction graph. |
| `PageState` | `ade_app.orchestration.state` | State object for concurrent page fan-out. |
| `WorkflowRequest` | `ade_app.orchestration.state` | Encapsulates the source document, page selection, DPI, and optional progress callback. |
| `ExtractionRun` | `ade_app.contracts` | The final extraction outcome containing all artifact texts, bytes, filenames, tokens, cost, and manifest. |
| `PageRunRecord` | `ade_app.contracts` | Per-page operational metrics including tokens, model attempts, timing, segments, and failure status. |
| `DocumentInput` | `ade_app.inputs` | Immutable container for an input file's name and raw bytes. |
| `RenderedPage` | `ade_app.raster` | In-memory rasterized page containing raw PNG bytes, dimensions, and effective DPI. |
| `PreparedPage` | `ade_app.preprocessing` | Rasterized page paired with applied preprocessing transforms and bounding box converters. |
| `PageTransform` | `ade_app.preprocessing` | Mathematical metadata for forward and inverse coordinate transforms (rotation, deskew, margins). |
| `LayoutAnalysis` | `ade_app.layout` | Regions detected by PP-StructureV3 with classification and bounding boxes. |
| `ExtractionDocumentV3` | `ade_app.models` | Strict output schema for v3 extraction JSON with nullable unresolved values and structured metadata. |
| `ExtractedFieldV3` | `ade_app.models` | Individual extracted field containing evidence provenance, status, validation checks, and routing scores. |
| `GroundTruthDocument` | `ade_app.models` | Base schema matching local GroundTruth JSON structure. |
| `PipelineConfig` | `ade_app.config` | Strict configuration hierarchy covering models, imaging, layout, routing, retries, runtime, and logging. |
| `TokenUsage` | `ade_app.cost` | Input, output, cached, and reasoning token counters used for cost accounting. |
| `BatchDocument` | `ade_app.batch` | Single document item within a multi-document batch request. |

## External systems

The codebase interacts with the following external systems:

1. **OpenAI API**:
   - Host: `https://api.openai.com/v1` (or an official OpenAI HTTPS endpoint specified by `OPENAI_BASE_URL`).
   - Only model: `gpt-6-sol`, `medium` reasoning for extraction and optional verification/repair.
   - Mechanism: Official `openai.OpenAI` SDK (`responses` interface) with `store=False`.
2. **Paddle / PP-StructureV3**:
   - Engine: Local PP-StructureV3 layout analysis model loaded via `paddleocr` and `paddlex`.
   - Execution: Runs locally on the host machine using either NVIDIA GPU (via `paddlepaddle-gpu`) or CPU (via `paddlepaddle`).
   - Network activity: Disabled at runtime by setting `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True"`.
3. **Local filesystem and network interfaces**:
   - Web server: Streamlit server binds locally to `127.0.0.1:9674` with XSRF protection. It does not accept remote network connections.
   - Storage: In-memory during pipeline execution. Files are written to disk only when saving via the CLI (`--output-dir`), downloading via Streamlit, or generating evaluation reports.
