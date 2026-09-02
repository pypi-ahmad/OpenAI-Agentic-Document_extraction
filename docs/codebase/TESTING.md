# Testing

Run `uv run --frozen pytest --cov`, `uv run --frozen ruff check .`, and
`uv run --frozen ty check`. Unit tests cover schemas, routing helpers, rendering, packaging,
session reset, cost, inputs, rasterization, and evaluation. Credentialed OCR accuracy is
measured separately with `ade-evaluate`.

## Evidence

- Test discovery and quality tools: `[tool.pytest]`, `[tool.ruff]`, and `[tool.ty]` in
  `pyproject.toml`.
- Focused suites: `tests/test_openai_client.py`, `tests/test_pipeline.py`,
  `tests/test_batch.py`, `tests/test_rendering.py`, and `tests/test_evaluation.py`.
- Credentialed runner: `run_evaluation` in `src/ade_app/evaluation.py`.

## Checkpoints

1. Unit contract → `uv run --frozen pytest`.
2. Static quality → `uv run --frozen ruff check .` and `uv run --frozen ty check`.
3. Accuracy → run `ade-evaluate` only with authorization, credentials, and mapped local inputs.
4. Reporting → keep offline checks separate from live OCR/accuracy evidence.
