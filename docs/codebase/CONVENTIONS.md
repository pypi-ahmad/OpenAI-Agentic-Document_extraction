# Conventions

Use strict Pydantic models at trust boundaries, immutable slotted dataclasses for runtime
records, deterministic ordering, bounded concurrency, and `Decimal` for cost. Prompts are
Markdown files. Never hardcode credentials or GroundTruth document values. Record failures and
review state. Do not correct uncertain content.

## Evidence

- Strict schema base: `StrictModel` in `src/ade_app/models.py`.
- Runtime records: slotted dataclasses in `src/ade_app/pipeline.py` and
  `src/ade_app/batch.py`.
- Decimal pricing: `MODEL_RATES` and `calculate_cost` in `src/ade_app/cost.py`.
- Prompt files: `src/ade_app/prompts/*.md`.
- Environment/secret resolution: `resolve_openai_api_key` in `src/ade_app/openai_client.py`.

## Change checkpoint

For each behavior change: identify the owning layer, preserve page and element ordering,
add a focused regression test, then run Pytest, Ruff, and ty without a live model call.
