# Configuration and command reference

## Runtime

| Setting | Value |
|---|---|
| Python | `3.13.15` |
| Package manager | `uv` |
| UI | Streamlit `>=1.62.0` |
| Address and port | `127.0.0.1:9674` |
| OpenAI endpoint | `https://api.openai.com/v1/responses` |
| Primary model | `gpt-5.6-luna`, low effort for printed/full-page extraction; Terra for difficult regions |
| Independent verification | `gpt-5.6-terra`, medium effort |
| Repair model | `gpt-5.6-sol`, low effort |
| Raster resolution | 300 DPI, reduced to respect the 20-million-pixel page limit |
| Segment threshold | 90.0, interpreted through the calibrated profile |
| Configured routing mode | `selective`; automatic acceptance still requires compatible promoted evidence |

Runtime model defaults are defined in `src/ade_app/config.py`; compatibility constants also live
in `src/ade_app/constants.py`. Change them only with compatible
structured output, pricing, tests, and measured evaluation evidence.

The 90-point threshold is still measured and recorded in `repair_all` mode, but no primary segment
bypasses independent verification. Selective routing starts only after a promoted profile has a
nonzero held-out acceptance count with zero observed false accepts across at least three held-out document families and
human-verified references. Related Amerigroup documents share one family. Missing, stale or
incompatible profiles disable automatic acceptance. Routing scores are not accuracy probabilities.

## Credentials

The client resolves `OPENAI_API_KEY` from the process environment, then Streamlit
`.streamlit/secrets.toml` when applicable.

```toml
OPENAI_API_KEY = "your-key"
```

The secrets file is Git-ignored. The application never needs to print the credential.

## Upload and batch limits

| Limit | Value |
|---|---:|
| Files | 20 |
| Per file | 200 MB |
| Batch | 500 MB |
| Selected pages | 100 |
| File workers | 1–4; UI uses up to 4 |
| Escalated segments | 16 per page; excess remains `needs_review` |

The Python input layer additionally accepts WebP, TIFF, and BMP; the Streamlit uploader exposes
only PDF, PNG, JPG, and JPEG.

## Pricing used for estimates

Configured USD rates per 1 million tokens:

| Model | Input | Cached input | Cache write | Output |
|---|---:|---:|---:|---:|
| `gpt-5.6-luna` | $0.20 | $0.02 | $0.25 | $1.20 |
| `gpt-5.6-terra` | $2.00 | $0.20 | $2.50 | $12.00 |
| `gpt-5.6-sol` | $4.00 | $0.40 | $5.00 | $20.00 |

The calculator charges each token category separately. Reasoning-token counts are captured when
available but are not a separate term in this formula. Verify configured values against current
provider pricing before financial use.

## Commands

```powershell
uv run --no-sync streamlit run streamlit_app.py --server.port 9674 --server.address 127.0.0.1
uv run --no-sync ade-profile --help
uv run --no-sync ade-evaluate --help
uv run --no-sync ade-calibrate-quality --help
uv run --no-sync ade-extract --help
```

`ade-extract --config settings.toml` accepts strict TOML sections named `models`, `imaging`,
`layout`, `routing`, `retries`, `runtime`, and `logging`. Unknown settings are rejected. CLI
values override TOML; `OPENAI_API_KEY` and `OPENAI_BASE_URL` are never read from this file.

The launcher verifies that a port 9674 owner is this repository's virtual-environment Streamlit
process before stopping it. An unrelated owner is reported and left running. The launcher then
starts Streamlit on loopback; errors produce a nonzero exit code.

The launcher uses `uv run --no-sync` to preserve the installed Paddle runtime. Explicitly sync
`cpu` or `gpu` before first launch and when updating dependencies.
CLI page concurrency defaults to three (`runtime.max_page_workers`); the UI batch path uses
two pages per document and up to four documents.

`.streamlit/config.toml` defines a charcoal/dark-indigo theme with dark-pink controls and cyan
links, and enables XSRF protection.

## Bounded evaluation and reuse

```powershell
uv run --no-sync ade-evaluate --suite curated --budget-usd 10 --acknowledge-sensitive-output
uv run --no-sync ade-evaluate --compare-routes --suite curated --budget-usd 10 --acknowledge-sensitive-output
uv run --no-sync ade-evaluate --resume evaluation/runs/RUN --budget-usd 10 --acknowledge-sensitive-output
uv run --no-sync ade-calibrate-quality --from-run evaluation/runs/RUN
```

Evaluation and the UI/CLI share the production extractor factory. `routing.mode` accepts
`baseline` (visual reads), `local_first` (validated local routes), and `selective` (also permits
validated model-based acceptance). Comparison variants share one budget, not one budget each.
Reports use schema v4; readers also accept v2 and v3.

The ledger reserves provider-counted input plus maximum output before dispatch. Unknown outcomes
remain charged at their reserved upper bound, including after restart. Concurrent processes cannot
share an active ledger. Preflight failure stops dispatch. Reservations include cache-write pricing,
long-context multipliers and the regional endpoint uplift; configured rates cannot lower the
verified pricing floor. The conservative ledger charge can exceed the nominal usage estimate.

Resume checks input, reference, configuration and routing fingerprints. It reuses completed
successful documents. Offline calibration reads captured primaries without API calls and writes a
candidate separately; machine-generated references cannot authorize promotion.

Streamlit reuses the most recent completed batch only for identical files, pages, profiles,
configuration and repair setting. Reset clears this session-local content. Validated independent
reads are reused only within a document job, bounded to 128 entries and cleared at completion.

Local route promotion accepts a separately labeled evidence packet through
`ade-calibrate-quality --local-evidence path/to/evidence.json`. The packet contains the production
routing fingerprint and samples with document family, route, unique crop SHA-256, exact-value
and critical-value checks, paired candidate/baseline field recall, and `human_verified`.
Every sampled case must preserve recall and match its values exactly; at least three independent
families are required. Failed evidence writes a candidate file and preserves the active profile.
No local route is promoted from the supplied generated references alone.

PP-Structure requires the declared `paddlex[ocr]` extra. Missing extras are reported in page
`layout_issues`; `full_page_fallback` and the document fallback count record actual routing.
Install with `uv sync --frozen --extra gpu`, or use the declared `cpu` extra if CUDA DLL loading
fails. The two Paddle runtime extras are mutually exclusive.
On Windows CPU, the adapter disables oneDNN because Paddle 3.3 cannot execute the layout
model's PIR array attributes through that backend. Standard CPU inference is supported.
Calibration fingerprints include Paddle and PaddleX versions as well as extraction settings.
Native layout images are capped at 1,600 pixels on their longest side to bound memory usage;
the coordinate transform preserves grounding, and model verification retains the configured
full-resolution images. Changing this bound invalidates calibration through the code fingerprint.
