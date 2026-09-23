---
type: Operations
title: Command-line workflow
description: Run ade-parse on a PDF or image, choose pages and output location, and interpret its result code.
tags: [cli, usage, outputs]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T14:47:54.995Z
sources:
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-2551327384d7e280fb8c1ffa
    resource: repo://src/ade_app/cli.py
  - id: openwiki-source-9ec6473d05fcc2cd40915af2
    resource: repo://tests/test_cli.py
generated: { by: "codex", at: "2026-09-23T14:47:54.995Z" }
---

# Command-line workflow

The package registers `ade-parse` as the `ade_app.cli:main` console command. From the repository's Windows/uv setup, the documented form is:

```powershell
uv run ade-parse .\scan.pdf --pages 1-3
```

The positional argument is the input file. `--pages` accepts comma-separated one-based page numbers and inclusive ranges (for example, `1,3-5`); its default is an empty string, which selects all pages. `--output-dir` chooses the destination directory. If omitted, outputs go beside the input in a directory named `<input-stem>.outputs`. `--overwrite` allows replacing files with matching output names.

The command validates the input, resolves the API key, parses the selected pages, builds the artifacts, creates the destination directory if needed, and writes Markdown, JSON, HTML, annotated PDF, and ZIP outputs. Without `--overwrite`, it checks all output paths before writing any of them and raises `FileExistsError` if one already exists. Partial and failed parses still write the available artifacts.

The exit code reflects the worst page outcome: `0` when all pages are `ok`, `2` when at least one page is `partial` and none failed, and `1` when any page is `failed`. On successful completion, the command prints the output directory.

The CLI tests check those exit-code cases and confirm the no-overwrite preflight does not create an earlier output when a later target already exists. See [generated artifacts](../outputs/artifacts.md) for the output contents and [document parsing lifecycle](../architecture/document-lifecycle.md) for page outcomes.

Sources: [cli.py](../../src/ade_app/cli.py#L13), [test_cli.py](../../tests/test_cli.py#L18), [pyproject.toml](../../pyproject.toml#L20).
