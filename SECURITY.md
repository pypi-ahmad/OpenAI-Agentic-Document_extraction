# Security policy

This application processes documents that may contain protected health information or other
sensitive personal data. It is designed for a single authorized operator on Windows and must run
only on a loopback address.

## Supported deployment

- Launch with `launch.cmd` and access only `http://127.0.0.1:9674`.
- Do not expose Streamlit directly to a LAN or the internet. A remote deployment requires a
  separately reviewed authentication, authorization, TLS, security-header, logging, and retention
  design.
- Store `OPENAI_API_KEY` in the process environment or `.streamlit/secrets.toml`. Never put it in
  source, prompts, logs, screenshots, or generated artifacts.
- Upload only documents you are authorized to send to OpenAI. The client requests `store=false`,
  but organizational approval and applicable vendor agreements remain operator responsibilities.

## Sensitive output handling

- Markdown, JSON, annotated PDFs, ZIPs, filenames, and evaluation reports may contain sensitive
  data. Treat all downloads and evaluation directories accordingly.
- Canonical Markdown is an untrusted interoperability artifact. Render it only through an
  allowlist sanitizer; do not feed it to `innerHTML`, a shell, SQL, or another executable sink.
- Reset clears Streamlit session state. It cannot delete files already downloaded by the browser.
- Evaluation requires `--acknowledge-sensitive-output`; delete retained runs according to your
  approved retention policy.

## Reporting a vulnerability

Do not include real credentials, patient data, or exploit payloads containing sensitive content in
an issue. Report the affected component, reproducible behavior using synthetic data, impact, and
the smallest safe reproduction. Rotate any credential that may have been exposed.

See [the security review](docs/security/SECURITY_REVIEW.md) for the current threat model, controls,
and residual risks.
