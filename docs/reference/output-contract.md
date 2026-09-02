# Output contract

The contract is discovered from local GroundTruth JSON/Markdown pairs and enforced by strict
Pydantic models. Unknown fields and type coercion are rejected.

## JSON document

Top-level keys appear as `markdown`, `metadata`, then `structure`. Metadata records the parse job,
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
- Checkboxes render as `[x]` and `[ ]`.
- Headings and emphasis derive from semantic styles.
- A final `<!-- doc_id=parse-… -->` identifies the parse job.

The UI sanitizes Markdown for safe preview. Downloads contain the canonical Markdown, not a
second UI representation.

## Manifests and failures

Manifest schema v6 records selected pages, model cascade, page/segment status, requests,
usage, cost, timing, annotation limitations, policy hashes, data classification,
`review_required`, and the explicit `review_state`. Usage includes input, cached-input,
cache-write, output, and reasoning tokens; API, routing, and retry calls are counted separately.
The batch manifest records each input,
selected pages, status, review state, failure, usage, cost, and output folder.

Successful pages remain when another page fails. Failed pages are explicit in JSON and manifests.
An unresolved segment retains its last trustworthy output and becomes `needs_review`; the system
does not invent a replacement.
