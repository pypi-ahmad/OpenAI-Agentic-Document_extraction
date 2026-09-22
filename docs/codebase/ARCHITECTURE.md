# Architecture

ADE is a local Streamlit front end around an in-memory extraction pipeline. Model responses are
semantic data. Local code handles validation, rendering, provenance, annotation, and packaging.

## Components

- `streamlit_app.py` collects authorization, uploads, and inclusive page ranges.
- `batch.py` validates batch limits and schedules documents.
- `inputs.py` and `raster.py` validate bytes and render selected pages.
- `pipeline.py` isolates page failures and restores requested page order.
- `openai_client.py` performs primary extraction, independent consensus, and
  disputed-field resolution.
- `quality.py`, `consensus.py`, and `verification.py` produce routing evidence.
- `models.py` and `rendering.py` enforce the GroundTruth-derived output contract.
- `outputs.py` creates review PDFs and exact ZIP payloads.
- `provenance.py` records hashes, runtime identity, and review state.
- `corpus.py`, `calibration.py`, and `evaluation.py` operate on mapped local references.

## Data flow

```text
upload → validate/rasterize → GPT-6 Sol structured page
  → unverified draft MD/JSON (primary snapshot)
  → quality gate → optional independent GPT-6 Sol segment crop
    → agreement: preserve primary
    → disagreement or flagged field: optional GPT-6 Sol repair crop
      → resolved patch or needs_review
  → deterministic JSON/Markdown → annotated review PDF → ZIP/manifest
```

Document content stays in memory unless the operator downloads artifacts or runs the
evaluation CLI. Manifests contain hashes, identifiers, usage, cost, and review state.
Both optional stages default off. All requests use medium reasoning and the same model.

## Concurrency

The UI accepts at most 20 uploads. The batch path permits at most four concurrent documents and
two primary page workers per document. A process-wide semaphore in `openai_client.py` admits at
most four Responses API calls. Requested page order is restored after futures complete.

## Evidence

- Upload and worker limits: constants and `extract_documents` in `src/ade_app/batch.py`.
- Global Responses call limit: `MAX_CONCURRENT_RESPONSES` in `src/ade_app/openai_client.py`.
- Page failure isolation and ordered assembly: `extract_document` in `src/ade_app/pipeline.py`.
- Responses API calls and `store=False`: `OpenAIPageExtractor` in
  `src/ade_app/openai_client.py`.
- Deterministic page/document rendering: `render_semantic_page` and `render_document` in
  `src/ade_app/rendering.py`.
- Annotation and packaging: public builders in `src/ade_app/outputs.py`.

## Checkpoints

1. Upload bounds → inspect `MAX_BATCH_FILES`, bytes, pages, and raster-pixel checks.
2. API concurrency → verify the four-document pool, two page workers per document, and the
   process-wide four-call Responses semaphore.
3. Ordering → verify outcomes and records are rebuilt from the requested page tuple.
4. Trust → verify structured output is validated before rendering and packaging.
5. Persistence → verify the UI builds artifacts in memory; profiling, calibration, and
   evaluation CLIs write only their explicitly requested artifacts.
