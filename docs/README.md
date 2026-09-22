# Documentation

Choose the document for your task.

## Learn

- [First extraction](tutorials/first-extraction.md): install, launch, process one document, and
  inspect the result.

## Use the app or tools

- [Use the Streamlit app](how-to/use-the-app.md): batches, page selection, progress, downloads,
  human review, and Reset.
- [Evaluate against GroundTruth](how-to/evaluate-groundtruth.md): profile mappings, run curated
  or full evaluations, interpret metrics, and calibrate routing.
- [Develop and validate changes](how-to/develop-and-test.md): managed environment, project
  boundaries, focused checks, prompt changes, and contract-safe development.
- [Troubleshooting](troubleshooting.md): startup, credentials, uploads, partial results, and slow
  runs.
- [Operations runbook](RUNBOOK.md): startup, shutdown, common error strings, and logging.
- [Contributing](CONTRIBUTING.md): development setup and verification commands.

## Understand ADE

- [Architecture](ARCHITECTURE.md): request/data flow, main types, and external systems.
- [Technical details](TECHNICAL.md): stack, invariants, error handling, and persistence.
- [Architecture explanation](explanation/architecture.md): request flow, model routing, deterministic output,
  concurrency, privacy, and trust boundaries.

## Reference

- [Configuration and commands](reference/configuration.md): limits, environment, CLI arguments,
  models, pricing, and launch behavior.
- [Output contract](reference/output-contract.md): JSON fields, Markdown conventions, ZIP
  contents, validation, and failure representation.
- [Python API](reference/python-api.md): supported programmatic entry points and core types.
- [Documentation coverage](reference/documentation-coverage.md): measured scope and known gaps.

## Governance and engineering evidence

- [ADE system card](governance/system-card.md)
- [Payer governance audit](governance/payer-audit.md)
- [Business-case memo](governance/business-case.md)
- [Control register](governance/control-register.md)
- [Acceptable use and data boundaries](governance/acceptable-use.md)
- [Validation and change control](governance/validation-change-control.md)
- [Governance operations runbook](governance/operations-runbook.md)
- [Codebase architecture](codebase/ARCHITECTURE.md)
- [Whole-codebase review](reviews/whole-codebase-review.md)
- [Security review](security/SECURITY_REVIEW.md)

The source code is authoritative if documentation and implementation ever disagree.
