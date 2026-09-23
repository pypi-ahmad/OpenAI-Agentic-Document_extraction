# GPT-6 Sol document parser

This Windows-native Streamlit app converts scanned PDFs and images into Markdown that preserves
context and layout. Every AI request uses `gpt-6-sol` with medium reasoning. The app does not
classify documents or extract fields against a business schema.

The parser preserves reading order and document elements such as headings, lists, tables, figures,
captions, footnotes, checkboxes, formulas, handwriting, and marginalia. It can reread up to four
difficult regions per page over two rounds. It marks unreadable content as `[ILLEGIBLE_TEXT]`.
When a dense page returns an incomplete structured block response, one bounded GPT-6 Sol fallback
returns layout-aware Markdown with a full-page grounding box and a review warning.

## Run the app

Requirements: Windows 11, Python 3.13, uv, and `OPENAI_API_KEY`.

```powershell
uv sync --group dev
.\launch.cmd
```

Open `http://127.0.0.1:9674`. Upload authorized documents, choose page ranges, preview the input,
and select **Parse documents**.

## Outputs

- Rendered and raw Markdown with copy and download controls
- Layout JSON with pages, ordered blocks, normalized boxes, warnings, and usage
- Annotated PDF with block labels and boxes
- Sanitized, self-contained HTML
- Figure and visual-region crops inside the output ZIP
- A ZIP containing the complete set of generated artifacts

The model's boxes are approximate visual grounding and require review when precise coordinates
matter. Switching views, copying, and downloading use the in-memory parse result and make no new
model calls. Estimated cost is unavailable when a failed model attempt does not return complete
usage data.

All model-facing instructions and request templates are Markdown files under
`src/ade_app/prompts/`. Python contains no embedded prompt text. Tests check the prompt filenames,
required placeholders, and context-image ordering.

## CLI

```powershell
uv run ade-parse .\scan.pdf --pages 1-3
```

Use `--output-dir` to choose the destination and `--overwrite` to replace existing exports.
The command exits with `0` when every page is `ok`, `2` when any page is `partial`, and `1` when
any page is `failed`. Partial and failed runs still write their available review artifacts.

## Development

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check src streamlit_app.py
```

See [architecture](docs/ARCHITECTURE.md), [output contract](docs/OUTPUTS.md), and
[security](SECURITY.md).
