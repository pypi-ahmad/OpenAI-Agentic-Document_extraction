# Configuration and command reference

## Runtime

| Setting | Value |
|---|---|
| Python | `>=3.14` |
| Package manager | `uv` |
| UI | Streamlit `>=1.62.0` |
| Address and port | `127.0.0.1:9674` |
| OpenAI endpoint | `https://api.openai.com/v1/responses` |
| Primary model | `gpt-5.6-terra`, medium effort |
| Repair model | `gpt-5.6-sol`, low effort |
| Raster resolution | 200 DPI, reduced to respect pixel/patch limits |
| Segment threshold | 90.0, interpreted through the calibrated profile |
| Active routing mode | `repair_all`; the current profile has no held-out accepted segments |

The active cascade is defined in `src/ade_app/constants.py`. Change it only with compatible
structured output, pricing, tests, and measured evaluation evidence.

The 90-point threshold is still measured and recorded in `repair_all` mode, but no primary segment
bypasses independent verification. Selective routing starts only after a promoted profile has a
nonzero held-out acceptance count within the false-accept limit.

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
| `gpt-5.6-luna` (configured pricing; inactive) | $0.20 | $0.02 | $0.25 | $1.20 |
| `gpt-5.6-terra` | $2.00 | $0.20 | $2.50 | $12.00 |
| `gpt-5.6-sol` | $4.00 | $0.40 | $5.00 | $20.00 |

The calculator charges each token category separately. Reasoning-token counts are captured when
available but are not a separate term in this formula. Verify configured values against current
provider pricing before financial use.

## Commands

```powershell
.\launch.cmd
uv run --frozen ade-profile --help
uv run --frozen ade-evaluate --help
uv run --frozen ade-calibrate-quality --help
```

The launcher verifies that a port 9674 owner is this repository's virtual-environment Streamlit
process before stopping it. An unrelated owner is reported and left running. The launcher then
starts Streamlit on loopback; errors produce a nonzero exit code.

`.streamlit/config.toml` defines a charcoal/dark-indigo theme with dark-pink controls and cyan
links, and enables XSRF protection.
