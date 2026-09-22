# ADE system card

Status: engineering description  
Owner: not evidenced  
Technical flow checked: 2026-09-23; organizational controls remain unevidenced.

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
Responses API using `store=False`. GPT-6 Sol with medium reasoning performs full-page extraction.
Optional verification rereads segment crops; optional repair rereads flagged fields. Both stages
default off and use the same model. Unverified draft exports are separate from verified v3 output.
The renderer produces the GroundTruth-derived contract. Downloads may contain sensitive document
content and filenames. The **Verified** view is a fail-closed artifact, not a human approval record.

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
layout, handwriting, and model behavior. No compatible GPT-6 Sol calibration profile is shipped.
Historical profiles do not authorize automatic acceptance for this model. With both optional
stages disabled, primary drafts remain readable while unresolved verified values stay null or
redacted. No live GPT-6 Sol accuracy evaluation is established by the model migration.

HIPAA role, BAA status, jurisdictions, retention period, workforce authorization, reviewer
qualifications, production monitoring, and incident ownership are not evidenced by this
repository.
