# Stack

Python 3.14, uv, Streamlit, OpenAI Responses API, Pydantic, Pillow, and PyMuPDF. Pytest,
coverage, Ruff, and ty provide local verification. Dependency metadata lives in
`pyproject.toml`; `uv.lock` is authoritative.

## Evidence

- Runtime and development dependencies: `[project.dependencies]` and `[dependency-groups]` in
  `pyproject.toml`.
- Tool configuration: `[tool.pytest]`, `[tool.ruff]`, and `[tool.ty]` in `pyproject.toml`.
- Streamlit runtime/theme configuration: `[server]` and `[theme]` in
  `.streamlit/config.toml`.
- Windows launcher: `launch.cmd` and `scripts/launch.ps1`.

## Checkpoint

Use `uv sync --frozen` and `uv run --frozen ...`; do not introduce a second environment or
dependency manager.
