# Security review report

## Executive summary

| Field | Value |
|---|---|
| Application | OpenAI Agentic Document Extraction |
| Review date | 2026-09-01 |
| Scope | Streamlit UI, uploads/rasterization, model boundary, output rendering, evaluation, launcher, dependencies |
| Deployment reviewed | Single authorized operator on Windows; loopback only |
| Overall residual risk | Low for the supported local deployment |

No Critical or High findings were confirmed. Six Medium/Low issues were corrected. The remaining
risks are operational: downloaded healthcare documents require an approved retention policy, and
remote deployment is unsupported without an authenticated TLS reverse proxy and security headers.

## Threat model

Assets are the OpenAI credential, uploaded documents, extracted healthcare data, generated files,
and the operator's workstation/API budget. Trust boundaries are the browser upload, PDF/image
decoder, OpenAI Responses API, structured model output, HTML preview, local filesystem evaluation
output, and PowerShell launcher.

Primary abuse cases are malformed decompression inputs, oversized structured output, repeated
repair calls, prompt injection printed inside a document, stored/DOM XSS through extracted
Markdown, leakage through errors or retained artifacts, and terminating an unrelated local
process.

## Findings

| ID | Severity | CWE | Finding | Status |
|---|---|---|---|---|
| SEC-001 | Medium (CVSS 5.3) | CWE-400 | Model output and repair fan-out could amplify memory, latency, and API cost | Resolved |
| SEC-002 | Medium (CVSS 5.3) | CWE-400 | Malformed PDFs could escape the upload boundary as decoder exceptions | Resolved |
| SEC-003 | Low (CVSS 3.3) | CWE-400 | Launcher could terminate an unrelated process occupying port 9674 | Resolved |
| SEC-004 | Low (CVSS 3.3) | CWE-209 | Internal failures and optimization-sensitive assertions weakened fail-closed behavior | Resolved |
| SEC-005 | Low (CVSS 3.1) | CWE-77 | Document-borne prompt instructions were not explicitly separated from extraction commands | Resolved, defense-in-depth |
| SEC-006 | Medium (CVSS 4.6) | CWE-359 | Evaluation writes potentially sensitive artifacts without explicit acknowledgement | Resolved; retention remains operational |

### SEC-001: bounded consumption

Model-facing arrays have evidence-based upper bounds in `src/ade_app/models.py`, document
parallelism is capped by `MAX_FILE_WORKERS` in `src/ade_app/batch.py`, and repair output is bounded
by constants in `src/ade_app/openai_client.py`. No more than 16 segments per page are escalated;
overflow is retained as `needs_review` with `repair_budget_exhausted` rather than silently
truncated.

### SEC-002: decoder boundary

PDF open and rendering failures are normalized to content-safe validation errors by
`get_page_count` and `rasterize_document` in `src/ade_app/raster.py`. Password protection, page
bounds, dimensions, file size, image format, and raster pixels remain fail-closed.

### SEC-003: launcher ownership

Before termination, `scripts/launch.ps1` verifies both the project virtual-environment interpreter
path and Streamlit app in the process command line. An unrelated owner causes a readable failure
and is not terminated.

### SEC-004: safe failure handling

The `is_loopback_address` startup check stops the UI when Streamlit is not configured for
loopback, and unexpected processing errors are mapped to a generic message in `streamlit_app.py`.
Runtime assertions on security-relevant execution paths were replaced with explicit exceptions.

### SEC-005: prompt injection boundary

All active prompts explicitly classify visible instructions as untrusted document text. Enforcement
still comes from code: the model has no tools, outputs must pass strict Pydantic schemas, and the
application deterministically renders validated data. Prompt wording is not treated as a security
boundary.

### SEC-006: sensitive evaluation output

The evaluation CLI requires `--acknowledge-sensitive-output` before `run_evaluation` creates a run
directory. Evaluation directories and common secret/private-key files are Git-ignored. The
operator must supply and enforce an appropriate retention/deletion policy.

## Existing controls verified

- Streamlit binds to `127.0.0.1`, XSRF and CORS protections are enabled, and WebSocket compression
  is disabled by the `[server]` settings in `.streamlit/config.toml`.
- API credentials come only from environment variables or Streamlit secrets. The OpenAI base URL
  accepts only official HTTPS `/v1` hosts.
- Every Responses API call in `OpenAIPageExtractor` uses `store=false`.
- Model responses are schema validated before rendering, packaging, or annotation.
- The UI preview passes canonical Markdown through the allowlist adapter in `preview.py` before
  `unsafe_allow_html=True`. Document-supplied links, images, and HTML tags are neutralized.
- ZIP member names are generated locally and validated; uploaded archives are never extracted.
- Reset clears session state and does not delete unrelated filesystem content.

## Automated review

- Bandit: no Medium or High findings before remediation; four Low assertion findings were fixed.
- Dependency audit: `pip-audit` reported no known vulnerabilities in the installed frozen runtime
  environment; the local editable `ade-app` package is not published and was skipped.
- Secret scan: no high-confidence credential or private-key patterns found in application source,
  tests, scripts, or documentation. Git-history scanning was unavailable because this workspace has
  no Git metadata.
- Adversarial tests cover malformed PDFs, output limits, repair budgets, loopback enforcement,
  error redaction, and active markup in previews.

## Residual risks and deployment rules

1. Streamlit does not expose a supported per-app mechanism for all response headers. Local HTTP
   intentionally has no HSTS. Any future remote deployment must add authentication, authorization,
   TLS, CSP, frame protection, `X-Content-Type-Options`, and a restrictive `Referrer-Policy` at a
   reviewed reverse proxy.
2. This application does not provide multi-user isolation, audit identity, encryption-at-rest, or
   automated retention. Do not use the current architecture as a shared service.
3. Schema validity and verifier scores do not prove OCR correctness. Human review remains mandatory
   for flagged or consequential output.
4. Clipboard contents and browser downloads are outside Streamlit session reset and must be handled
   under workstation and organizational data policy.
