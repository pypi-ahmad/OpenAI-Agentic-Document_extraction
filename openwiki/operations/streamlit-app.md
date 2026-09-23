---
type: Operations
title: Local Streamlit application
description: The local upload, page selection, authorization, parse, preview, and results workflow.
tags: [streamlit, local-app, workflow]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T14:47:54.995Z
sources:
  - id: openwiki-source-964922c41d4be25ac5f159ec
    resource: repo://streamlit_app.py
generated: { by: "codex", at: "2026-09-23T14:47:54.995Z" }
---

# Local Streamlit application

The Streamlit interface is implemented in `streamlit_app.py`. The documented launch uses the repository's Windows setup (`uv sync --group dev` followed by `launch.cmd`) and opens `http://127.0.0.1:9674`.

## Upload and review

On startup, the app checks Streamlit's configured server address and stops unless it is a loopback address. The sidebar accepts PDF and image uploads, limited to 20 files and 200 MB per file. Each file is validated and decoded to determine its page count. Users choose a contiguous start/end page range for each document; the combined batch is limited to 100 pages. A preview is available before parsing.

The **Parse documents** button remains disabled until the user checks the statement that they are authorized to send the selected pages to OpenAI and will review the result. The button also requires valid inputs and a batch within the page limit. The app obtains the API key from the configured Streamlit secret or environment through the parser's credential resolver, then processes each selection.

## Session results

The app stores results in Streamlit session state. A cache key is derived from the document bytes and selected page tuple; when the same key is already present in that session, parsing is skipped. **Reset** clears the session's parse-result mapping and reruns the app; it is not a general deletion of other application or provider data.

Results display usage metrics, estimated cost when usage is complete, and parsing warnings. Tabs show rendered or raw Markdown, annotated PDF, HTML, and JSON. Individual formats and a ZIP of all outputs can be downloaded. Switching views and downloading use the already-built in-memory result rather than invoking another parse.

## Deployment boundary

The loopback check prevents this app from proceeding when its configured bind address is non-loopback. It is an application guard, not a complete deployment security or provider-retention guarantee. Keep this workflow local and review the separate [data and network boundaries](../security/data-and-network-boundaries.md) before changing its exposure or data handling.

<!-- openwiki: broken internal link [../../README.md#L13] heading anchor "L13" does not exist in "../../README.md". Fix the href or restore the target, then delete this comment. -->
Sources: [streamlit_app.py](../../streamlit_app.py#L152), [README.md](../../README.md#L13), [inputs.py](../../src/ade_app/inputs.py#L15).
