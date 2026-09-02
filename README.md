# OpenAI Agentic Document Extraction

Local Streamlit application for extracting scanned PDFs and images into a strict,
GroundTruth-derived JSON structure and deterministic Markdown. The active pipeline uses
OpenAI `gpt-5.6-terra` at medium reasoning effort, then sends only low-quality or disagreeing
segments to `gpt-5.6-sol` at low effort.

> [!IMPORTANT]
> This project is inspired by agentic document extraction workflows. It does not claim
> accuracy equivalence with LandingAI. Use the included evaluation workflow to measure results
> on your own GroundTruth pairs, and review every output marked `review_required`.

## What it does

- Upload 1–20 PDF, PNG, JPG, or JPEG files in one batch.
- Select an inclusive start and end page for each document.
- Process documents and pages concurrently within fixed limits while preserving page order.
- Validate structured responses before rendering or packaging them.
- Produce Markdown, JSON, an annotated PDF, usage details, and a ZIP manifest.
- Route uncertain segments through independent verification and targeted repair.
- Compare output with local GroundTruth JSON/Markdown through a reproducible CLI.
- Load an existing evaluation `report.json` in the sidebar for a local summary and download.

Selected page images and routed review crops are sent to OpenAI. Results remain in server-side
state associated with the current Streamlit session until Reset, the upload selection changes, or
the session ends; downloaded files are written only when you choose to save them.

## Quick start

Requirements: Windows 11, Python 3.14+, [`uv`](https://docs.astral.sh/uv/), and an OpenAI API
key with access to the configured models.

```powershell
cd D:\AI\Github\OpenAI-Agentic-Document_extraction
$env:OPENAI_API_KEY = "your-key-for-this-shell"
uv sync --frozen
.\launch.cmd
```

Open <http://127.0.0.1:9674>. Upload documents, choose page ranges, and select **Extract
documents** after confirming the authorization-and-review checkbox. Do not put credentials in
source files; `.streamlit/secrets.toml` is also supported and ignored by Git.

For the complete first-run walkthrough, see [First extraction](docs/tutorials/first-extraction.md).

## Development checks

```powershell
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ty check
```

## Documentation

- [Documentation map](docs/README.md)
- [Use the Streamlit app](docs/how-to/use-the-app.md)
- [Develop and validate changes](docs/how-to/develop-and-test.md)
- [Evaluate against GroundTruth](docs/how-to/evaluate-groundtruth.md)
- [Architecture](docs/explanation/architecture.md)
- [Output contract](docs/reference/output-contract.md)
- [Configuration and commands](docs/reference/configuration.md)
- [Python API](docs/reference/python-api.md)
- [Documentation coverage](docs/reference/documentation-coverage.md)
- [Troubleshooting](docs/troubleshooting.md)
- [System card](docs/governance/system-card.md)
- [Payer governance evidence audit](docs/governance/payer-audit.md)
- [Business-case evidence memo](docs/governance/business-case.md)
- [Codebase architecture map](docs/codebase/ARCHITECTURE.md)
- [Whole-codebase review](docs/reviews/whole-codebase-review.md)
- [Security policy](SECURITY.md)
- [Security review](docs/security/SECURITY_REVIEW.md)

## Repository layout

```text
streamlit_app.py       Streamlit entry point
src/ade_app/           extraction, validation, rendering, evaluation, and packaging
src/ade_app/prompts/   versioned Markdown prompts used by model requests
tests/                 focused unit and integration tests
docs/                  user and developer documentation
evaluation/            evaluation notes and generated run directories
data/                  local source and GroundTruth inputs (Git-ignored)
profiles/              generated schema and quality profiles (Git-ignored)
```

## Known boundaries

- OCR accuracy depends on scan quality, model behavior, and calibrated quality thresholds.
- A valid schema does not prove that every transcribed value is correct.
- Bounding boxes can be omitted from the annotated PDF when they are not trustworthy.
- Cost shown by the app is an estimate based on configured per-token rates.
- The launcher is intended for local Windows use and binds Streamlit to loopback only.
