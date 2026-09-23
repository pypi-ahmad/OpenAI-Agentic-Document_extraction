---
type: Reference
title: Generated artifacts
description: Files exported from a document parse and the rules that shape HTML, PDF annotations, ZIP contents, and estimated cost.
tags: [artifacts, export, html, pdf, json]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T14:47:54.995Z
sources:
  - id: openwiki-source-af7ce3097a085c7ed95ccb88
    resource: repo://src/ade_app/artifacts.py
  - id: openwiki-source-104417024fe98bcbdd7d4cb6
    resource: repo://tests/test_artifacts.py
generated: { by: "codex", at: "2026-09-23T14:47:54.995Z" }
---

# Generated artifacts

`build_artifacts` derives each export from one `DocumentResult`, the image assets produced during parsing, and the rendered pages. It returns Markdown, JSON, HTML, an annotated PDF, a ZIP, and their output filenames. The CLI and Streamlit UI consume this same artifact bundle.

## Formats

- **Markdown** is the parser's combined page Markdown, including page breaks and references to visual assets.
- **JSON** serializes the complete document result, including page blocks, geometry, warnings, statuses, and usage.
- **HTML** renders Markdown as CommonMark with tables enabled. Parser-produced asset references are replaced with base64 PNG data URLs, and CSS is inline. The rendered markup is passed through `nh3` with an explicit tag and attribute allowlist; the allowed URL scheme is `data`. This narrows emitted markup, but is not a general-purpose guarantee for arbitrary HTML or every browser execution context.
- **Annotated PDF** places each rendered page image onto a PDF page, then draws a rectangle and block ID/type label for every parsed block.
- **ZIP** contains the Markdown, JSON, HTML, annotated PDF, and extracted visual assets. It does not add the original uploaded document as a separate archive member.

Names use a sanitized input stem, with a fallback of `document` if the stem contains no safe characters. The ZIP name ends in `.outputs.zip`; the PDF uses `.annotated.pdf`.

## Estimated cost

`estimated_cost` returns no value when aggregate usage is incomplete, since missing failed-attempt usage would make the estimate misleading. With complete usage, it calculates an estimate from uncached input, cached input, cache-write, and output token counts using the configured rates. The UI labels this as estimated cost rather than a provider invoice.

## Tests

Artifact tests build exports from a single result, check that a script element is absent from the sanitized HTML, confirm an image is embedded as a data URL, and verify that the ZIP includes the visual asset and annotated PDF. A separate test confirms that incomplete usage suppresses the cost estimate.

Sources: [artifacts.py](../../src/ade_app/artifacts.py#L36), [test_artifacts.py](../../tests/test_artifacts.py#L10), [config.py](../../src/ade_app/config.py#L17).
