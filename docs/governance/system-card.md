# ADE system card

Status: engineering description  
Owner: not evidenced  
Last reviewed: 2026-09-01

## Purpose and intended users

ADE converts user-selected scanned PDF/image pages into validated JSON, deterministic Markdown,
and an annotated review aid. It is intended for authorized operators who understand the source
documents and will verify flagged output.

## Prohibited uses

ADE must not independently approve, deny, delay, price, modify, or terminate care, coverage,
claims, or prior authorization. Schema validity and a quality score do not prove factual
correctness. Output with review state `required_unresolved` or `failed` is unreviewed.

## System and data flow

The local Streamlit app rasterizes selected pages in memory and sends them to the official OpenAI
Responses API using `store=False`. Terra performs extraction and an independent reread of
sub-threshold segments; Sol receives only disputed field crops. The renderer produces the
GroundTruth-derived contract. Downloads may contain sensitive document content and filenames.

Credentials come only from environment variables or Streamlit secrets. The app binds to
loopback. Reset clears ADE-owned session state but cannot delete files already downloaded by an
operator or written by the evaluation CLI.

## Validation and oversight

- Structured model responses are validated before rendering and packaging.
- Structural conflicts, failed pages, and unresolved fields fail closed into review states.
- Manifests record model/effort, prompt and policy hashes, source/page/output hashes, request IDs,
  token usage, cost, timing, failures, and review state.
- GroundTruth evaluation measures accuracy; it does not establish universal quality.
- No authenticated reviewer identity or durable reviewer disposition is implemented.

## Lifecycle and limitations

Prompt, model, quality-profile, schema, renderer, and policy changes require the validation gate
in [Validation and change control](validation-change-control.md). OCR accuracy varies by scan,
layout, handwriting, and model behavior. The current quality profile reports zero primary
acceptances and therefore operates as `repair_all`.

HIPAA role, BAA status, jurisdictions, retention period, workforce authorization, reviewer
qualifications, production monitoring, and incident ownership are not evidenced by this
repository.

