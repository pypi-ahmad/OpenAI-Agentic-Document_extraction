# Graph Report - OpenAI-Agentic-Document_extraction  (2026-09-23)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 159 nodes · 391 edges · 11 communities (8 shown, 3 thin omitted)
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 52 edges (avg confidence: 0.95)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `1da6df43`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9

## God Nodes (most connected - your core abstractions)
1. `DocumentInput` - 18 edges
2. `parse_document()` - 17 edges
3. `_parse_page()` - 17 edges
4. `_read_crop()` - 15 edges
5. `DocumentResult` - 14 edges
6. `_read_page_markdown()` - 14 edges
7. `Box` - 13 edges
8. `RenderedPage` - 13 edges
9. `StrictModel` - 12 edges
10. `PageRead` - 11 edges

## Surprising Connections (you probably didn't know these)
- `_preview()` --uses--> `DocumentInput`  [INFERRED]
  streamlit_app.py → src/ade_app/inputs.py
- `test_exports_derive_from_one_result_and_sanitize_html()` --uses--> `Block`  [INFERRED]
  tests/test_artifacts.py → src/ade_app/models.py
- `test_exports_derive_from_one_result_and_sanitize_html()` --uses--> `Box`  [INFERRED]
  tests/test_artifacts.py → src/ade_app/models.py
- `test_box_requires_ordered_normalized_coordinates()` --uses--> `Box`  [INFERRED]
  tests/test_models.py → src/ade_app/models.py
- `test_image_render_and_crop()` --uses--> `Box`  [INFERRED]
  tests/test_raster.py → src/ade_app/models.py

## Import Cycles
- None detected.

## Communities (11 total, 3 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.12
Nodes (20): BaseModel, decimal, model_validator, pydantic, Small, fixed configuration for the single-model parser., Block, Box, CropRead (+12 more)

### Community 1 - "Community 1"
Cohesion: 0.18
Nodes (24): Any, base64, collections_abc, openai, os, RuntimeError, ParserConfig, PageResult (+16 more)

### Community 2 - "Community 2"
Cohesion: 0.19
Nodes (19): html, io, json, markdown_it, nh3, _annotate(), Artifacts, build_artifacts() (+11 more)

### Community 3 - "Community 3"
Cohesion: 0.17
Nodes (14): dataclasses, Image, math, pymupdf, DocumentInput, Document input validation and inclusive page-range parsing. This is the trust…, Validated in-memory PDF or image supplied to the extraction pipeline., crop_page() (+6 more)

### Community 4 - "Community 4"
Cohesion: 0.11
Nodes (15): Exception, hashlib, ipaddress, re, is_loopback_address(), public_extraction_error(), Security boundary helpers shared by the local UI. Responsible for verifying…, Return whether Streamlit is configured to listen only on loopback. (+7 more)

### Community 5 - "Community 5"
Cohesion: 0.18
Nodes (14): parametrize, pathlib, load_prompt(), page_request_prompt(), Load and render version-controlled Markdown prompt templates., Load one non-empty Markdown prompt by its fixed application name., Render trusted application values into a Markdown prompt template., Select the template that exactly describes the attached image sequence. (+6 more)

### Community 6 - "Community 6"
Cohesion: 0.21
Nodes (13): argparse, Path, _exit_code(), main(), Command-line entry point for parse-only document conversion., _write_outputs(), parse_page_range(), Parse 1-based inclusive pages such as ``1,3-5``. (+5 more)

### Community 7 - "Community 7"
Cohesion: 0.40
Nodes (4): fixture, pil, pytest, png_bytes()

## Knowledge Gaps
- **1 isolated node(s):** `ade-app`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 54 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `DocumentInput` connect `Community 3` to `Community 0`, `Community 1`, `Community 4`, `Community 5`, `Community 6`?**
  _High betweenness centrality (0.075) - this node is a cross-community bridge._
- **Why does `Box` connect `Community 0` to `Community 1`, `Community 2`, `Community 3`?**
  _High betweenness centrality (0.053) - this node is a cross-community bridge._
- **Why does `PageRead` connect `Community 0` to `Community 1`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `DocumentInput` (e.g. with `parse_document()` and `_open_image()`) actually correct?**
  _`DocumentInput` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `parse_document()` (e.g. with `DocumentInput` and `RenderedPage`) actually correct?**
  _`parse_document()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `_parse_page()` (e.g. with `ParserConfig` and `PageRead`) actually correct?**
  _`_parse_page()` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `_read_crop()` (e.g. with `ParserConfig` and `CropRead`) actually correct?**
  _`_read_crop()` has 4 INFERRED edges - model-reasoned connections that need verification._