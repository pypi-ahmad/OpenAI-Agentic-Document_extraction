# OpenAI Agentic Document Extraction

ADE (Agentic Document Extraction) is a local Streamlit application and command-line pipeline for scanned PDFs and images. Every OpenAI request uses `gpt-6-sol` with medium reasoning. Extraction always runs; the default `baseline` mode reads full pages. Verification and repair are independent optional stages, both off by default. PyMuPDF, OpenCV, PP-StructureV3, and an in-memory LangGraph workflow handle imaging, layout, validation, and output assembly.

Each run exports an unverified draft, fail-closed GroundTruth-compatible v3 JSON and Markdown, confidence reports, and annotated review PDFs. No GPT-6 Sol calibration profile ships with the app. Historical profiles cannot authorize automatic acceptance for this model. Review draft content against the source before use.

## Requirements

The project uses the following runtime requirements.

- **Operating system**: Windows 11 is required for the `launch.cmd` launcher script (uses PowerShell CIM and network connection commands) and the precompiled CUDA 12.9 GPU wheel. Python CLI utilities can run on other platforms using the CPU extra.
- **Python**: Python `>=3.13.15,<3.14` (`.python-version` specifies `3.13.15`).
- **Package manager**: [`uv`](https://docs.astral.sh/uv/) (project build backend requires `uv_build>=0.12.7,<0.13.0`).
- **Accelerator (optional)**: NVIDIA GPU with CUDA 12.9 support for `paddlepaddle-gpu`. A CPU-only fallback is available via the `cpu` extra.
- **Credentials**: OpenAI API key with access to `gpt-6-sol`.

## Setup and run commands

### 1. Clone the repository

```powershell
git clone https://github.com/pypi-ahmad/OpenAI-Agentic-Document_extraction.git
cd OpenAI-Agentic-Document_extraction
```

### 2. Configure credentials

Set the OpenAI API key in your terminal session:

```powershell
$env:OPENAI_API_KEY = "your-api-key"
```

Alternatively, add `OPENAI_API_KEY = "your-api-key"` to `.streamlit/secrets.toml` (ignored by Git).

### 3. Synchronize dependencies

Choose either the GPU or CPU runtime extra:

- For Windows with NVIDIA GPU (CUDA 12.9):
  ```powershell
  uv sync --frozen --extra gpu
  ```
- For CPU-only hosts:
  ```powershell
  uv sync --frozen --extra cpu
  ```

### 4. Run the Streamlit web interface

On Windows, use the launcher script:

```powershell
.\launch.cmd
```

On other platforms, or to run Streamlit directly without port checking:

```powershell
uv run --no-sync streamlit run streamlit_app.py --server.port 9674 --server.address 127.0.0.1 --server.enableXsrfProtection true
```

The application will be accessible at <http://127.0.0.1:9674>.

### 5. Run single-document extraction CLI

Extract a single PDF or image to an output directory:

```powershell
uv run --no-sync ade-extract .\document.pdf --output-dir .\document.outputs
```

Exit codes:
- `0`: Extraction complete and valid.
- `2`: Extraction produced usable output but requires human review (`partial`).
- `1`: Extraction failed (`failed`).

### 6. Run evaluation and calibration CLIs

Evaluate extraction accuracy against local GroundTruth pairs:

```powershell
uv run --no-sync ade-evaluate --acknowledge-sensitive-output --suite curated
```

Calibrate the segment-quality routing profile:

```powershell
uv run --no-sync ade-calibrate-quality --suite full
```

Verify the GroundTruth schema and profile contract:

```powershell
uv run --no-sync ade-profile --check
```

## Configuration

### Environment variables

- `OPENAI_API_KEY`: Required string containing the OpenAI API credential (unless provided in `.streamlit/secrets.toml`). Must not contain ASCII control characters or surrounding whitespace.
- `OPENAI_BASE_URL`: Optional string defining the OpenAI endpoint (defaults to `https://api.openai.com/v1`). If provided, must use HTTPS and point to an official OpenAI API hostname (`api.openai.com` or `*.api.openai.com`).
- `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK`: Set to `"True"` by default in `src/ade_app/preprocessing.py` to prevent PaddleX from checking remote model hosts prior to reading local caches.

### Configuration files

- `.streamlit/config.toml`: Configures the Streamlit server port (`9674`), loopback address (`127.0.0.1`), security settings (XSRF protection enabled), maximum upload size (`200 MB`), and UI dark theme.
- `.streamlit/secrets.toml`: Optional, Git-ignored file to supply `OPENAI_API_KEY`.
- TOML pipeline configuration (passed to CLI commands via `--config <path>`): Supports overriding defaults for `[model], [stages]`, `[imaging]`, `[layout]`, `[routing]`, `[retries]`, `[runtime]`, and `[logging]`.

## Repository map

```text
streamlit_app.py       Streamlit web interface entry point
launch.cmd             Windows launcher batch script
src/ade_app/           Core Python package
  cli.py               Command-line extraction entry point (ade-extract)
  evaluation.py        GroundTruth evaluation runner (ade-evaluate)
  calibration.py       Segment-quality router calibration (ade-calibrate-quality)
  profile.py           Contract profile verification tool (ade-profile)
  orchestration/       LangGraph workflow definitions, nodes, and state machines
  services/            Service boundaries (imaging, layout, validation, confidence)
  prompts/             Versioned Markdown prompts for OpenAI models
tests/                 Pytest test suite (unit, integration, governance, link checks)
docs/                  Project documentation, guides, architecture, and runbooks
schemas/               JSON schemas for ground truth and artifacts
profiles/              Calibrated quality and routing profiles
scripts/               Utility scripts (launch.ps1)
data/                  Local input documents and GroundTruth pairs (Git-ignored)
evaluation/            Generated evaluation reports and traces (Git-ignored)
```

## Run tests

Execute the test suite using pytest via `uv`:

```powershell
uv run --no-sync pytest
```

Run code formatting and style checks:

```powershell
uv run --no-sync ruff check .
```

Run static type checking:

```powershell
uv run --no-sync ty check
```

## Limits

- **In-memory state and crash recovery**: Extraction state and intermediate page images exist in memory only. LangGraph is compiled without a persistent checkpointer; crashed runs cannot resume mid-workflow and must be restarted.
- **Strict batch constraints**: Multi-document processing in the web UI enforces bounds of at most 20 files, 500 MB total size, 100 pages, 4 concurrent documents, 2 page workers per document, and 4 concurrent OpenAI API calls.
- **Process-lifetime layout latches**: In `ade_app.layout`, model initialization failure (`_MODEL_FAILURE`) or GPU-to-CPU fallback (`_CPU_LATCHED`) latches for the lifetime of the process and will apply to all subsequent documents until process restart.
- **Single-threaded Paddle inference**: PP-StructureV3 layout inference is not safe for concurrent multithreaded execution; calls are serialized through a global lock (`_INFERENCE_LOCK`).
- **Raster pixel budget**: Pages exceeding 20,000,000 pixels or batches exceeding 100,000,000 pixels automatically scale down effective DPI to prevent memory exhaustion.
- **Cost estimation**: Displayed financial costs are approximations calculated using configured per-million-token rates.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Technical details](docs/TECHNICAL.md)
- [Operations runbook](docs/RUNBOOK.md)
- [Contributing](docs/CONTRIBUTING.md)
- [Documentation map](docs/README.md)
- [First extraction walkthrough](docs/tutorials/first-extraction.md)
- [Use the Streamlit app](docs/how-to/use-the-app.md)
- [Develop and validate changes](docs/how-to/develop-and-test.md)
- [Evaluate against GroundTruth](docs/how-to/evaluate-groundtruth.md)
- [Output contract](docs/reference/output-contract.md)
- [Configuration and commands](docs/reference/configuration.md)
- [Python API](docs/reference/python-api.md)
- [Documentation coverage](docs/reference/documentation-coverage.md)
- [Troubleshooting](docs/troubleshooting.md)
- [System card](docs/governance/system-card.md)
- [Security policy](SECURITY.md)
