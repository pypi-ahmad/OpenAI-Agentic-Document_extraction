# Whole-codebase review

Date: 2026-09-01. Scope: the current repository snapshot, including production Python,
Streamlit entry point, launchers, configuration, and tests.

This workspace contains neither Git metadata nor a `.planning` directory. The review is therefore
a whole-snapshot inspection, not a fabricated commit diff or GSD phase review.

## Verified safeguards

- The Streamlit runtime refuses a non-loopback server address.
- Upload count, bytes, page count, document workers, and estimated raster pixels are bounded.
- The current batch path limits execution to four documents, permits two primary page workers
  per document, and caps Responses API calls process-wide at four.
- Structured model responses are validated before deterministic rendering and packaging.
- Failed and unresolved pages/segments remain visible in manifests and UI review state.
- Provider storage is disabled with `store=False` on production Responses API calls.
- API credentials are resolved from environment/Streamlit secrets and are not serialized.
- Markdown preview neutralizes document-supplied HTML outside the renderer allow-list.
- ZIP member names are validated before archive construction.
- Manifest provenance includes source/raster/artifact/prompt/policy hashes, usage, cost, and
  review state.

## Correctness repairs represented in the current snapshot

- Document-level evaluation failures retain page metrics and available usage.
- Batch progress can revise previously completed pages to failed after post-processing failure.
- Raster batches are rejected when their estimated total pixel count exceeds the configured cap.
- Top-level and page manifests include cache-write token accounting.
- Evaluation configuration hashes the prompts used by production extraction.
- Empty nonblank page responses are rejected.
- Review-required annotations are omitted per affected element and recorded as limitations.
- Independent consensus and disputed-field routing operate on stable segment/field IDs.
- Structural table cells can be retained in JSON without forcing a corresponding Markdown cell.
- NPI validation uses the correct checksum parity.
- Table-cell atomic groundings must stay on their containing page.

## Open operational risks

- The active calibration profile reports zero safe primary acceptances, so routing is effectively
  repair-all until a better credentialed calibration is accepted.
- Human-review status is signaled but reviewer identity, disposition, and durable approval history
  require an external governed workflow.
- In-memory uploads and generated artifacts can contain sensitive healthcare data; deployment
  retention, access control, and incident response are outside this repository.
- Offline schema and unit checks cannot establish transcription accuracy or model stability.

## Evidence

- Loopback boundary: the `is_loopback_address` startup check in `streamlit_app.py` and
  `.streamlit/config.toml`.
- Resource/concurrency limits: `extract_documents` in `src/ade_app/batch.py` and the Responses
  semaphore in `src/ade_app/openai_client.py`.
- Page ordering and failure isolation: `extract_document` in `src/ade_app/pipeline.py`.
- Provider/storage boundary: client construction and request methods in
  `src/ade_app/openai_client.py`.
- Output validation and provenance: assembly in `src/ade_app/pipeline.py`.
- Preview/archive hardening: `src/ade_app/preview.py` and public builders in
  `src/ade_app/outputs.py`.
- Test inventory and commands: `tests/` and tool sections in `pyproject.toml`.

## Validation record

- No live OpenAI call or credentialed GroundTruth accuracy run was performed during this
  documentation refresh.
- No live OCR/structured-output quality claim is made here.
- A future live result must be reported separately with mapped inputs, prompt/model hashes,
  token usage, cost, failures, and per-document metrics.

## Review checkpoints

1. Source evidence, not documentation, decides executable behavior.
2. Offline tests prove deterministic code contracts, not OCR accuracy.
3. `review_required` means human action remains; it is not an approval record.
4. Any network deployment needs a new authentication, authorization, retention, and threat review.
