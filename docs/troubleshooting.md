# Troubleshooting

## `uv is not available on PATH`

Install `uv`, open a new PowerShell session, then run:

```powershell
uv --version
uv sync --frozen --extra gpu
```

Use `--extra cpu` instead when the declared CUDA runtime is unavailable; select one runtime extra.

## `Required credential OPENAI_API_KEY is unavailable`

Set the key in the environment that launches Streamlit, or use ignored
`.streamlit/secrets.toml`. Check only existence; do not print the value.

```powershell
if (Test-Path Env:OPENAI_API_KEY) { "OPENAI_API_KEY is set" }
```

## The app does not open on port 9674

Run `.\launch.cmd` from the root and read its error. If the port does not become available,
inspect it without terminating unrelated processes:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 9674 |
  Select-Object LocalAddress, LocalPort, OwningProcess
```

## Upload is rejected

Confirm the UI format is PDF, PNG, JPG, or JPEG and the file is nonempty. Limits are 200 MB per
file, 500 MB per batch, 20 files, and 100 selected pages. Corrupt/encrypted PDFs may also fail.

## Extraction is slow

Large pages require image processing. Enabled verification or repair stages add GPT-6 Sol calls. Reduce pages
or batch size, then inspect **Usage** for repairs. Do not exceed account concurrency limits merely
to hide latency.

## A document is partial or needs review

Inspect failure reasons, segment scores, unresolved fields, and model attempts in **Usage**.
Compare output with the source/annotated PDF. Successful pages remain downloadable. Repeated
retries are not evidence of correctness.

## Draft is readable but verified output is redacted

This is expected when independent evidence or compatible calibration is missing. Drafts preserve
primary transcription without claiming correctness. Both optional stages default off. Enable
**Verify extraction** or **Repair flagged fields** for a new run if additional paid reads are
appropriate; they do not guarantee acceptance. Review unresolved values against the source.

## Annotated PDF is missing a box

Read limitation captions below the preview. Zero-area, unavailable, and failed-page boxes are
deliberately omitted rather than fabricated.

## Markdown tables appear as source text

Use the **Markdown** preview, not a plain-text viewer. Canonical output intentionally uses HTML
tables. If Streamlit displays literal tags, start current code through `.\launch.cmd`, Reset, and
extract again.

## Evaluation excludes a document

Inspect report `exclusions` and `mappings`. Supply exactly one matching source PDF and one
JSON/Markdown GroundTruth pair with an unambiguous stem. Exclusions never enter metrics.

## Tests fail after a contract change

Only change the contract when GroundTruth evidence supports it:

```powershell
uv run --frozen ade-profile --check
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ty check
```

Do not weaken strict validation to accommodate an invalid response.
