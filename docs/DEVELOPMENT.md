# Development

Use uv for environment and dependency operations.

```powershell
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check src streamlit_app.py
```

Tests use fake Responses API objects, so they make no paid calls. A live document run requires an
explicitly configured `OPENAI_API_KEY` and incurs API charges.

Runtime prompts belong only in `src/ade_app/prompts/*.md`. Add or revise the Markdown template,
then update prompt tests for its required placeholders and context-image ordering. Do not embed
model instructions or request prose in Python.
