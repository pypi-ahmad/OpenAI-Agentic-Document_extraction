# How to develop and validate changes

Use this workflow for local code, prompt, schema, and documentation changes. Routine checks do
not require an OpenAI credential and do not send documents outside the machine.

## Prepare the managed environment

From the repository root in PowerShell:

```powershell
uv sync --frozen
```

Use `uv run --frozen` for project commands. Do not install packages directly into the managed
environment or place credentials in source files.

## Choose the narrowest change boundary

- `streamlit_app.py` and `session.py` own UI and session behavior.
- `batch.py` and `pipeline.py` own scheduling, ordered aggregation, and failure isolation.
- `openai_client.py` and `prompts/*.md` own Responses API orchestration and instructions;
  `quality.py` and the evidence helpers own scoring, verification, and document-local evidence.
- `models.py`, `rendering.py`, and `outputs.py` own the strict artifact contract.
- `evaluation.py` and `evaluation_metrics.py` own reproducible GroundTruth measurement.

Read the relevant tests before changing behavior. Preserve deterministic output ordering and
explicit partial-failure records.

## Run focused checks

Run the smallest relevant test first, then the local quality gates:

```powershell
uv run --frozen pytest tests/test_inputs.py
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ty check
```

These commands use mocked model responses. They do not make live OpenAI calls.

## Change prompts safely

Prompts belong in `src/ade_app/prompts/*.md`; do not embed production prompts in Python strings.
Keep the structured schema unchanged unless GroundTruth evidence requires a contract change.
All active prompt hashes are recorded in manifests. A material `page_extraction.md` change also
requires focused client tests and quality-profile recalibration before a production run because
the profile validates that prompt hash. Changes to `segment_consensus.md` or
`field_resolution.md` require focused routing tests and measured evaluation; they do not by
themselves invalidate the primary-extraction quality profile.

## Change the output contract safely

1. Inspect representative GroundTruth JSON and Markdown pairs.
2. Update strict models and deterministic rendering together.
3. Add a non-sensitive structural fixture based only on observed fields.
4. Run schema, rendering, packaging, and evaluation tests.
5. Update the output-contract and Python API references.

Never edit or overwrite GroundTruth files as part of a test.

## Run credentialed evaluation only when authorized

Live evaluation sends selected page images to OpenAI and writes generated document content under
the chosen output directory. Review [Evaluate against GroundTruth](evaluate-groundtruth.md), use
the explicit sensitive-output acknowledgement, and confirm the intended page scope before
starting it.

## Update documentation

Keep reader journeys separate: tutorials teach, how-to guides solve tasks, explanations describe
design, and references state exact contracts. Verify relative links, command help, configuration
values, and examples against the current source before finishing.
