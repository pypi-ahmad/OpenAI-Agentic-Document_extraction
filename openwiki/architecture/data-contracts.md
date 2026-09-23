---
type: Architecture
title: Parsing data contracts
description: The validated structures and bounds used for parser inputs, page reads, usage, and document results.
tags: [models, validation, parser, schema]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T14:47:54.995Z
sources:
  - id: openwiki-source-265d19b495d114c0f8cf8939
    resource: repo://src/ade_app/config.py
  - id: openwiki-source-24ad0df9507c984d030f2a87
    resource: repo://src/ade_app/models.py
  - id: openwiki-source-d3c9c57e1b60bfc378d0ee28
    resource: repo://tests/test_models.py
generated: { by: "codex", at: "2026-09-23T14:47:54.995Z" }
---

# Parsing data contracts

The parser uses strict Pydantic models in `src/ade_app/models.py`: undeclared fields are rejected. These models define the structured response from page and crop reads as well as the normalized result passed to artifact generation.

## Geometry and content blocks

`Box` stores `xmin`, `ymin`, `xmax`, and `ymax` as coordinates in the inclusive range 0–1. Its validator requires `xmax > xmin` and `ymax > ymin`, so every box has positive area. A `Block` has a patterned identifier, one of the declared content types, non-empty Markdown, a box, and optional description and asset fields. The type vocabulary includes text structures, tables, figures, diagrams, formulas, footnotes, marginalia, checkboxes, attestations, logos, and scan codes.

`ZoomRequest` carries a box, a non-empty reason of at most 200 characters, and an optional quarter-turn rotation (0, 90, 180, or 270 degrees). Each `PageRead` and `CropRead` allows at most four zoom requests in its corresponding request list. Crop reads return replacement blocks and may request another zoom.

## Usage and outcomes

`Usage` records input, cached-input, cache-write, output, and reasoning token counts, plus call count. Counts must be non-negative. Its `complete` flag is combined with logical AND when usage records are added; therefore one incomplete component keeps the aggregate incomplete.

`PageResult` records a one-based page number, positive rendered width and height, blocks, warnings, usage, and a status of `ok`, `partial`, or `failed`. `DocumentResult` identifies schema version `1.0` and model `gpt-6-sol`, and carries the source filename, page results, combined Markdown, document warnings, and aggregate usage.

## Parser configuration bounds

`ParserConfig` fixes the model to `gpt-6-sol` and reasoning effort to `medium`. Its defaults are 300 DPI, at most two zoom rounds, at most four crops per page, and a 300-second timeout. Validation permits DPI from 150 through 400, zero through two zoom rounds, zero through four crops, and a positive timeout no greater than 900 seconds. The same config also defines the cost-rate values used by the artifact layer.

## Tests

The focused model tests reject reversed box coordinates and a page response containing five zoom requests. Those tests cover these validation boundaries; they are not an exhaustive test of every field above.

Sources: [models.py](../../src/ade_app/models.py#L10), [config.py](../../src/ade_app/config.py#L10), [test_models.py](../../tests/test_models.py#L7).
