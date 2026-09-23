---
type: Reference
title: Prompt templates and image contract
description: Runtime prompt files, their selection and rendering rules, and the tests that keep page context aligned with attached images.
tags: [prompts, templates, model-input]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T14:47:54.995Z
sources:
  - id: openwiki-source-0799e49e6a3cda394f75126b
    resource: repo://src/ade_app/prompts.py
  - id: openwiki-source-eed11abd1c9e773f326b2974
    resource: repo://src/ade_app/prompts/crop_read.md
  - id: openwiki-source-c063de1159a8cddcbf46d35d
    resource: repo://src/ade_app/prompts/page_parse.md
  - id: openwiki-source-c03055224e32283b242f3a21
    resource: repo://src/ade_app/prompts/page_request_previous_next.md
  - id: openwiki-source-5faf38664389435a855abe11
    resource: repo://tests/test_prompts.py
generated: { by: "codex", at: "2026-09-23T14:47:54.995Z" }
---

# Prompt templates and image contract

All runtime prompts are version-controlled Markdown files under `src/ade_app/prompts/`. `prompts.py` accepts only a plain `.md` filename, rejects empty templates, and formats application-provided values. Missing format values are reported as a `ValueError`.

## Page request variants

`page_request_prompt` selects one of four templates based on whether a preceding and/or following selected page is attached. The image sequence is explicitly labeled: context pages are for continuity and must not be transcribed; only the target page is the subject of the structured result. The main `page_parse.md` prompt treats document contents as untrusted data, asks for layout-aware blocks and normalized boxes, and defines when to request a zoom.

The crop path uses `crop_request.md` to supply the source page, original normalized crop box, applied rotation, and reason. `crop_read.md` asks for corrected or additional blocks only, retaining an existing ID when correcting a block and expressing returned boxes in full-page coordinates. If structured page output is incomplete, the separate `page_markdown_request.md` and `page_markdown.md` files define the single-page Markdown fallback.

The test suite expects exactly nine runtime prompt files. It checks all load as non-empty, each page-context variant has the corresponding one, two, or three image markers, the rendered request has no unresolved placeholders, missing crop values fail, and paths are rejected by the loader.

Prompt files are executable application behavior: review them alongside parser changes. This page describes their contract and does not replace or modify their instructions. See [document parsing lifecycle](../architecture/document-lifecycle.md) for where each prompt is used.

<!-- openwiki: broken internal link [../../src/ade_app/prompts/page_parse.md#L1] heading anchor "L1" does not exist in "../../src/ade_app/prompts/page_parse.md". Fix the href or restore the target, then delete this comment. -->
<!-- openwiki: broken internal link [../../src/ade_app/prompts/crop_read.md#L1] heading anchor "L1" does not exist in "../../src/ade_app/prompts/crop_read.md". Fix the href or restore the target, then delete this comment. -->
Sources: [prompts.py](../../src/ade_app/prompts.py#L10), [test_prompts.py](../../tests/test_prompts.py#L12), [page_parse.md](../../src/ade_app/prompts/page_parse.md#L1), [crop_read.md](../../src/ade_app/prompts/crop_read.md#L1).
