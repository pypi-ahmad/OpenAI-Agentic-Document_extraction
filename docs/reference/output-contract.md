# Output contract

Local GroundTruth JSON/Markdown pairs define the contract. Strict Pydantic models reject unknown
fields and type coercion.

## JSON document

New artifacts use `schema_version: 3`, `markdown`, `metadata`, `structure`, and `fields`.
The explicit reader also accepts v2 without adding verification evidence. Metadata records the parse job,
model version, source page count, character count, range units, duration, billing, and failures.
Structure contains ordered document, page, element, and table-cell nodes.

Grounding boxes use normalized page coordinates from 0 to 1. Text ranges use Unicode code-point
offsets into top-level Markdown. IDs are document-wide, type-specific, zero-based sequences such
as `text-0`, `table-0`, and `table_cell-0`.

## Validation invariants

- `output_markdown_chars` equals the Markdown length.
- Pages are unique and in source order.
- Failed pages have a reason, no children, and a zero-length range.
- `failed_pages` exactly matches failed page nodes.
- Element ranges remain within parents and Markdown.
- Grounding page numbers match containing pages.
- Page boxes cover the page; element boxes remain normalized.
- IDs are correctly typed, unique, and sequential.

## Markdown conventions

- UTF-8, LF line endings, and no terminal newline in observed artifacts.
- `<!-- PAGE BREAK -->` separates selected pages.
- Tables use deterministic HTML `<table>`, `<tr>`, and `<td>` structures.
- A structural JSON table cell may deliberately omit its Markdown `<td>` when the inspected
  GroundTruth convention represents geometry without presentation content.
- Checkboxes render as `[x]`, `[ ]`, or `[?]` when ambiguous.
- Headings and emphasis derive from semantic styles.
- A final `<!-- doc_id=parse-… -->` identifies the parse job.

The UI sanitizes Markdown before preview. Downloads contain canonical Markdown.

## Manifests and failures

Manifest schema v10 (v8/v9 remain readable) records selected pages, one model, medium reasoning,
verification/repair flags, draft hashes, page/segment status, requests,
usage, cost, timing, annotation limitations, policy hashes, data classification,
`review_required`, and the explicit `review_state`. Usage includes input, cached-input,
cache-write, output, and reasoning tokens; API, routing, and retry calls are counted separately.
The batch manifest records each input,
selected pages, status, review state, failure, usage, cost, and output folder.

Successful pages remain when another page fails. Failed pages are explicit in JSON and manifests.
Unresolved lines and cells use `[UNVERIFIED]` in Markdown; agreeing neighbors remain visible.
The v3 field value is null for blank, illegible, ambiguous, conflicting or unverified values.
Candidate transcriptions remain in evidence, with page/region/semantic IDs and crop hashes when
source pixels are available. `value_state` explains why a value is null.

V3 field `confidence` is a nullable correctness probability on 0–1; without calibration it
remains null. Confidence reports use null for otherwise uncalibrated page/document confidence
and `0.0` for failed pages or documents containing a failed page.
`routing_score` and evidence-level legacy `confidence` are 0–100 routing signals,
not probabilities. Independent agreement records verification without fabricating a percentage.
Confidence reports use version `2.0`; version `1.1` remains readable. Rejected fields, missing
evidence, unresolved coverage, failed pages and annotation limitations trigger review.

## Unverified draft exports

`<name>.draft.md` and `<name>.draft.json` preserve primary extraction before verification or repair.
They are included in document and batch ZIPs. Draft JSON uses a separate `draft_schema_version: 1`,
`artifact_kind: "unverified_draft"`, and `verification_status: "unverified"` contract, not v3.
Each page contains its page-local extraction and ranges, or an explicit failure. Draft Markdown
is labeled unverified. These exports are not accepted evidence and do not relax v3 redaction or
null-field rules. Manifest `draft_sha256` hashes both draft representations.
