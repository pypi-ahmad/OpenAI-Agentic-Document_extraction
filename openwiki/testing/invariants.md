---
type: Testing
title: Tested invariants
description: A map of the focused pytest modules and the behavioral checks they currently contain.
tags: [tests, pytest, verification]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T14:47:54.995Z
sources:
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-104417024fe98bcbdd7d4cb6
    resource: repo://tests/test_artifacts.py
  - id: openwiki-source-9ec6473d05fcc2cd40915af2
    resource: repo://tests/test_cli.py
  - id: openwiki-source-d3c9c57e1b60bfc378d0ee28
    resource: repo://tests/test_models.py
  - id: openwiki-source-8bb0b8f087c8e236af369473
    resource: repo://tests/test_parser.py
  - id: openwiki-source-5faf38664389435a855abe11
    resource: repo://tests/test_prompts.py
  - id: openwiki-source-2f1d32941155767631369cd0
    resource: repo://tests/test_raster.py
  - id: openwiki-source-9ff42c2d9fcb6dd247f2ae73
    resource: repo://tests/test_security.py
generated: { by: "codex", at: "2026-09-23T14:47:54.995Z" }
---

# Tested invariants

Pytest discovers tests under `tests/` and runs quietly by default. The current suite is organized around small behavioral boundaries:

| Test module | Checked behavior |
| --- | --- |
| `test_models.py` | Rejects reversed normalized boxes and more than four zoom requests in one page read. |
| `test_parser.py` | Uses fake response clients to check a bounded page-plus-crop parse, partial Markdown fallback, and retention of a failed page. |
| `test_raster.py` | Exercises raster image rendering and a crop operation. |
| `test_prompts.py` | Checks the exact runtime template set, context/image variants, required template values, and path rejection. |
| `test_artifacts.py` | Checks that exports derive from one result, HTML sanitization and image embedding, ZIP members, and cost suppression for incomplete usage. |
| `test_cli.py` | Checks outcome exit codes and preflights all conflicting output paths before writing. |
| `test_security.py` | Checks loopback-address examples and that a secret path in an ordinary exception is hidden. |

These tests establish specific examples and boundaries, not exhaustive behavior or live-provider quality. In particular, parser tests monkeypatch client construction with fake responses; they do not make API calls. See [data contracts](../architecture/data-contracts.md), [document lifecycle](../architecture/document-lifecycle.md), and [data and network boundaries](../security/data-and-network-boundaries.md) for the implementation each group exercises.

Run the repository's documented checks from PowerShell:

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check src streamlit_app.py
```

The command list is the repository's documented development workflow; this initialization pass inspected test sources but did not execute the suite.

Sources: [pyproject.toml](../../pyproject.toml#L26), [test_parser.py](../../tests/test_parser.py#L15), [test_artifacts.py](../../tests/test_artifacts.py#L10), [test_cli.py](../../tests/test_cli.py#L18).
