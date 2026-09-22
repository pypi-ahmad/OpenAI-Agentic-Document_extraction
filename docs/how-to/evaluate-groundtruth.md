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
uv run --no-sync --locked ade-profile
uv run --no-sync --locked ade-profile --check
```

The first command generates the profile and JSON Schema from actual pairs. The second verifies
the existing artifacts without rewriting them. Custom paths are available through `--profile`,
`--schema`, and the positional GroundTruth directory; run `ade-profile --help` for details.

## Run an evaluation

The curated suite selects 14 configured pages. The full suite selects every mapped page.

```powershell
uv run --no-sync --locked ade-evaluate --suite curated --acknowledge-sensitive-output
uv run --no-sync --locked ade-evaluate --suite full --acknowledge-sensitive-output
```

Custom example:

```powershell
uv run --no-sync --locked ade-evaluate `
  --ground-truth-dir "D:\path\to\GroundTruths" `
  --source-dir "D:\path\to\Original Pdfs" `
  --output-root "D:\path\to\evaluation-runs" `
  --suite curated `
  --acknowledge-sensitive-output
```

Each run receives a timestamped, configuration-hashed directory containing generated artifacts,
records, a machine-readable report, and a Markdown summary.

Evaluation uses the same single `gpt-6-sol` model and stage settings as production. Without a
configuration file, verification and repair are both off. To evaluate enabled stages, pass
`--config settings.toml` containing the desired `[stages]` values. The default shared budget is
$10 per run; set `--budget-usd` explicitly when needed. `--compare-routes` is retained only to
return a migration error; run separate configured evaluations instead.

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
uv run --no-sync --locked ade-calibrate-quality --suite full --max-workers 3
```

The active profile is written only when extraction has no failures, held-out validation accepts
at least one segment with zero observed false accepts, and the promotion gate passes across at
least three document families. Human-verified evidence is required; generated references alone
cannot authorize promotion. Inspect `evaluation/calibration-report.json` when promotion fails.
Do not weaken the gate merely to make calibration pass.

No compatible GPT-6 Sol profile ships with the migration. `--from-run evaluation/runs/RUN`
performs offline calibration from captured primaries and writes a separate candidate, without
API calls or promotion of the active profile. Historical metrics do not establish GPT-6 Sol accuracy.

Measured results apply only to that corpus and configuration. They do not prove universal
accuracy or equivalence to another extraction system.
