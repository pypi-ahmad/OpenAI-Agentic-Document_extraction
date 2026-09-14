# Contributing

This document describes how to set up a development environment and run verification checks on this codebase.

## Development environment setup

1. Ensure Python `3.13.15` and `uv` are installed on your host.
2. Synchronize the development virtual environment:
   - For hosts with an NVIDIA GPU and CUDA 12.9:
     ```powershell
     uv sync --frozen --extra gpu
     ```
   - For CPU-only hosts:
     ```powershell
     uv sync --frozen --extra cpu
     ```

## Local verification checks

Run the following test, lint, and type checks prior to submitting changes:

### Test suite

Run unit and integration tests using pytest:

```powershell
uv run --no-sync pytest
```

### Code formatting and linting

Check code formatting and style rules using Ruff:

```powershell
uv run --no-sync ruff check .
```

### Static type checking

Run type checking using ty:

```powershell
uv run --no-sync ty check
```

### GroundTruth contract check

If modifying schemas or model parsing logic, verify that profile artifacts match local GroundTruth definitions:

```powershell
uv run --no-sync ade-profile --check
```

## Continuous integration and branch policies

- **Automated CI**: There are currently no automated continuous integration workflows (e.g., GitHub Actions) configured in the repository tree.
- **Branch policies**: There are no branch naming conventions, merge restrictions, or automated branch protection scripts defined in the repository.
