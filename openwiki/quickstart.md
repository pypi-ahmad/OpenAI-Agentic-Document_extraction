---
type: Overview
title: Repository quickstart
description: What the document parser does, how to run either interface, and where to look for implementation and test details.
tags: [overview, quickstart, navigation]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T14:47:54.995Z
sources:
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
  - id: openwiki-source-8bb0b8f087c8e236af369473
    resource: repo://tests/test_parser.py
generated: { by: "codex", at: "2026-09-23T14:47:54.995Z" }
---

# Repository quickstart

This Windows-native application converts selected scanned PDF or image pages into layout-aware Markdown, structured JSON, and review artifacts. It uses GPT-6 Sol for document parsing; it is not a business-field extraction system.

## Run locally

The documented setup requires Windows 11, Python 3.13, `uv`, and an `OPENAI_API_KEY`:

```powershell
uv sync --group dev
.\launch.cmd
```

The Streamlit app is documented at `http://127.0.0.1:9674`. For command-line parsing after setup:

```powershell
uv run ade-parse .\scan.pdf --pages 1-3
```

The CLI also supports `--output-dir` and `--overwrite`; see [Command-line workflow](operations/command-line.md). These commands describe the repository workflow and do not verify local credentials or make a live model request.

## Find your way around

- [Document parsing lifecycle](architecture/document-lifecycle.md) traces validation, rendering, model reads, bounded rereads, and outcomes.
- [Parsing data contracts](architecture/data-contracts.md) documents the typed blocks, geometry, usage, and limits.
- [Streamlit application](operations/streamlit-app.md) covers upload, selection, session results, and downloads.
- [Generated artifacts](outputs/artifacts.md) explains Markdown, JSON, HTML, annotated PDF, ZIP, and cost estimates.
- [Prompt contract](prompts/prompt-contract.md) maps runtime prompt templates to their roles and tests.
- [Data and network boundaries](security/data-and-network-boundaries.md) records input and local-UI controls with their limits.
- [Tested invariants](testing/invariants.md) maps focused pytest modules to behaviors they check.

## Tests and tooling

Run the focused test suite and static checks with:

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check src streamlit_app.py
```

Pytest discovers tests from `tests/`. Parser tests use fake clients, so the local test suite does not establish live-provider behavior.

<!-- openwiki: broken internal link [../README.md#L1] heading anchor "L1" does not exist in "../README.md". Fix the href or restore the target, then delete this comment. -->
Sources: [README.md](../README.md#L1), [pyproject.toml](../pyproject.toml#L5), [parser tests](../tests/test_parser.py#L15).
