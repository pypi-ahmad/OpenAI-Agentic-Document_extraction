---
type: Security
title: Data and network boundaries
description: Code-enforced input, rendering, loopback, provider-request, and UI-error boundaries, with their verification limits.
tags: [security, inputs, network, privacy]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T14:47:54.995Z
sources:
  - id: openwiki-source-da70c2f43735a6b7f9927cca
    resource: repo://src/ade_app/inputs.py
  - id: openwiki-source-45364f84513e1179b49c3a95
    resource: repo://src/ade_app/parser.py
  - id: openwiki-source-aee72531fa7a01a681e988c1
    resource: repo://src/ade_app/raster.py
  - id: openwiki-source-b31d2e116096bf8f2e4aa6d2
    resource: repo://src/ade_app/security.py
  - id: openwiki-source-964922c41d4be25ac5f159ec
    resource: repo://streamlit_app.py
  - id: openwiki-source-9ff42c2d9fcb6dd247f2ae73
    resource: repo://tests/test_security.py
generated: { by: "codex", at: "2026-09-23T14:47:54.995Z" }
---

# Data and network boundaries

This page records controls visible in the current implementation. They are narrow application checks, not a general security certification or a guarantee about provider-side handling.

## Input and rendering limits

`DocumentInput` accepts only a plain, safe filename with a supported PDF/image suffix and non-empty bytes no larger than 200 MB. It validates the name and suffix; actual decoding happens later in the raster layer. Images are rejected above 20 million pixels. PDF rendering also caps each rendered page at 20 million pixels by reducing scale when necessary. Password-protected PDFs are unsupported.

## Local UI and provider request

Before rendering the Streamlit UI, the app stops unless the configured server address is loopback (`localhost` or an IP address recognized as loopback). The UI requires an explicit checkbox confirming authorization to send pages to OpenAI before enabling its parse action. This is a user-facing gate, not a technical access-control system.

The parser validates `OPENAI_API_KEY` input and restricts a configured `OPENAI_BASE_URL` to an HTTPS OpenAI API host and the `/v1` root. Page, crop, and Markdown fallback requests set `store=False`. That parameter is part of the outgoing request; this code does not establish provider retention, account, transport, or organizational-policy guarantees.

## Errors and verification

The UI exposes selected actionable configuration errors, while other extraction exceptions are replaced with a generic message so provider and path details are not shown in the ordinary error text. The security test checks that `127.0.0.1` is accepted, `0.0.0.0` is rejected, and a `ValueError` containing a secret path does not leak that text. Those assertions cover examples of the helpers, not every deployment or data-handling scenario.

Do not treat the loopback guard as permission to expose the app on a network. If changing deployment or data flow, review the UI behavior in [the Streamlit workflow](../operations/streamlit-app.md) and the actual parser and raster code.

Sources: [inputs.py](../../src/ade_app/inputs.py#L15), [raster.py](../../src/ade_app/raster.py#L15), [security.py](../../src/ade_app/security.py#L14), [parser.py](../../src/ade_app/parser.py#L43), [test_security.py](../../tests/test_security.py#L4).
