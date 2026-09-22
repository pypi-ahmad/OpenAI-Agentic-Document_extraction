# Structure

- `src/ade_app/`: application modules
- `src/ade_app/prompts/`: active Markdown model instructions
- `tests/`: focused unit and integration tests
- `docs/`: tutorials, how-to, reference, explanation, reviews, and governance
- `data/`, `profiles/`, `evaluation/runs/`: local/generated evidence, Git-ignored

The root `streamlit_app.py` is the interactive entry point. CLI entry points in `pyproject.toml`
cover profiling, calibration, and evaluation. `launch.cmd` calls the PowerShell launcher and
binds the app to port 9674 on loopback.

## Evidence

- Entry points: `[project.scripts]` in `pyproject.toml`.
- UI entry point: `streamlit_app.py`.
- Launcher binding: `scripts/launch.ps1`.
- Session reset boundary: `reset_session_state` in `src/ade_app/session.py`.

## Checkpoint

Place model-independent domain logic under `src/ade_app`, prompts under `prompts`, and tests
under `tests`. Generated document content must stay in ignored data/evaluation locations.
