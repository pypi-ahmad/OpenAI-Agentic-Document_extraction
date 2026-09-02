# How to evaluate against GroundTruth

Evaluation runs the production pipeline and makes real OpenAI API calls. It creates a new run
directory and never overwrites GroundTruth inputs.

## Prepare matched inputs

Defaults:

```text
data/Original Pdfs/
data/GroundTruths/
```

Each source PDF must map unambiguously to one `.parse.json` and one `.parse.md` pair. Unmatched or
ambiguous inputs are exclusions and do not enter scored metrics.

## Discover or verify the contract

```powershell
uv run --frozen ade-profile
uv run --frozen ade-profile --check
```

The first command generates the profile and JSON Schema from actual pairs. The second verifies
the existing artifacts without rewriting them. Custom paths are available through `--profile`,
`--schema`, and the positional GroundTruth directory; run `ade-profile --help` for details.

## Run an evaluation

The curated suite selects 14 configured pages. The full suite selects every mapped page.

```powershell
uv run --frozen ade-evaluate --suite curated --acknowledge-sensitive-output
uv run --frozen ade-evaluate --suite full --acknowledge-sensitive-output
```

Custom example:

```powershell
uv run --frozen ade-evaluate `
  --ground-truth-dir "D:\path\to\GroundTruths" `
  --source-dir "D:\path\to\Original Pdfs" `
  --output-root "D:\path\to\evaluation-runs" `
  --suite curated `
  --acknowledge-sensitive-output
```

Each run receives a timestamped, configuration-hashed directory containing generated artifacts,
records, a machine-readable report, and a Markdown summary.

The acknowledgement is mandatory because those files can reproduce sensitive document content.
Store them only in an approved location and delete them under your retention policy.

## Interpret metrics

- **Valid JSON rate** measures schema validity, not transcription correctness.
- **Exact/normalized field F1** compares aligned element values.
- **Markdown CER** is character edit distance divided by reference length; lower is better.
- **Heading/table metrics** compare meaningful Markdown/HTML structure.
- **Page-order accuracy** checks selected source order.
- **Cost, usage, duration, failures, and retries** expose operational tradeoffs.

Normalization standardizes line endings and Unicode, collapses horizontal whitespace within
lines, removes trailing spaces, and removes only the nondeterministic final `doc_id` comment. It
preserves values, case, punctuation, headings, HTML tables, page breaks, and reading order.

## Calibrate the quality gate

```powershell
uv run --frozen ade-calibrate-quality --suite full --max-workers 3
```

The quality profile is written only when extraction has no failures, held-out validation accepts
at least one segment, and the validation false-accept rate is at most 0.10. Otherwise inspect
`evaluation/calibration-report.json`; do not weaken the promotion rules merely to make calibration
pass.

Measured results apply only to that corpus and configuration. They do not prove universal
accuracy or equivalence to another extraction system.
