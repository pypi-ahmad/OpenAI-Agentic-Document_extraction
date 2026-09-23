---
type: Architecture
title: Document parsing lifecycle
description: How selected PDF or image pages become validated, rendered, parsed page results and a combined document result.
tags: [parser, document-processing, rasterization, recovery]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T14:47:54.995Z
sources:
  - id: openwiki-source-265d19b495d114c0f8cf8939
    resource: repo://src/ade_app/config.py
  - id: openwiki-source-da70c2f43735a6b7f9927cca
    resource: repo://src/ade_app/inputs.py
  - id: openwiki-source-45364f84513e1179b49c3a95
    resource: repo://src/ade_app/parser.py
  - id: openwiki-source-8bb0b8f087c8e236af369473
    resource: repo://tests/test_parser.py
generated: { by: "codex", at: "2026-09-23T14:47:54.995Z" }
---

# Document parsing lifecycle

The application accepts a validated in-memory `DocumentInput`, renders selected pages, reads each rendered page with GPT-6 Sol, and combines page outcomes into a `DocumentResult`. The main orchestration is in `src/ade_app/parser.py`; input and raster boundaries are handled separately by `inputs.py` and `raster.py`.

## Input and rendering

`DocumentInput` rejects unsafe or non-plain filenames, unsupported suffixes, empty bytes, and inputs over 200 MB. Supported formats are PDF and common raster images. Page-range parsing uses one-based inclusive page numbers; an empty range selects all pages.

Rasterization treats an image as one page and decodes requested PDF pages. Password-protected PDFs are rejected. Images are converted to RGB (with EXIF orientation applied), and rendering is capped at 20 million pixels per page. PDF rendering starts from configured DPI and reduces scale if the page would exceed that pixel cap. Crop boxes are normalized to the rendered image; requested crop rotation is applied before encoding the crop as PNG.

## Page read and bounded rereads

`parse_document` renders the requested pages, builds the provider client, then processes rendered pages in order. Each page request attaches the preceding selected page when present, the target page, and the next selected page when present. The prompt identifies the target page; context pages do not change which page the result represents. Requests pass `store=False` and send PNG images at `detail: original`; this describes request parameters, not a broader provider-retention guarantee.

The structured page response contains blocks, warnings, and optional zoom requests. A page read is validated with one structured parse attempt. If its structured output is incomplete, the parser tries a separate full-page, layout-aware Markdown fallback. That fallback becomes a single block with a full-page box, and the page is marked partial because the structured attempt's token usage is unavailable. If the fallback also fails or returns empty text, the page is retained as failed with no blocks and incomplete usage.

The parser limits requested crop rereads to the configuration's per-page crop cap and round cap (defaults: four crops and two rounds). Each crop is expanded slightly within the page bounds, read as structured output, and any replacement blocks replace existing blocks with matching IDs. A failed crop leaves the full-page reading in place and adds a warning. Remaining zoom requests at the configured limit also add a warning. Warnings make a successfully read page `partial`; without warnings it is `ok`.

## Document assembly

Visual blocks produce PNG assets from their page boxes. The parser then renders Markdown from the page blocks, inserts page-break markers between pages, aggregates warnings, and combines usage. A failed page remains in the page list, so downstream exports can report the incomplete outcome rather than silently dropping it. See [data contracts](data-contracts.md) for result fields and [generated artifacts](../outputs/artifacts.md) for export formats.

## Test evidence

Parser tests replace the client with fake response objects. They cover a successful bounded page-plus-crop read, a partial Markdown fallback, and a failed page retained in the document. These tests exercise the local parser decisions; they do not establish live provider behavior.

Sources: [inputs.py](../../src/ade_app/inputs.py#L15), [raster.py](../../src/ade_app/raster.py#L15), [parser.py](../../src/ade_app/parser.py#L75), [test_parser.py](../../tests/test_parser.py#L52).
