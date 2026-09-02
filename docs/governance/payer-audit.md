# Payer AI governance audit

## Review metadata

- Repository: `OpenAI-Agentic-Document_extraction`
- Commit: unavailable; this workspace has no Git metadata
- Review date: 2026-09-01
- Supplied context: local Streamlit document extraction
- Source refresh: not required for a binding finding; no payer decision path was identified
- Nature: engineering evidence review, not legal advice or a compliance certification

## Applicability

The repository extracts documents but does not adjudicate claims, apply coverage criteria,
generate denial reasons or notices, manage appeals, or change care access. Payer/program type,
plan funding, jurisdictions, drug/non-drug service, decision date, and regulated role are unknown.
Accordingly, adverse-decision requirements are out of scope rather than presumed binding.

## Coverage manifest

Reviewed the Streamlit entry point; extraction, routing, validation, rendering, packaging,
evaluation, session, security, and provenance modules; prompts; tests; launcher/configuration;
and governance documentation. Local source documents, GroundTruth contents, generated runs,
caches, binaries, and secrets were excluded from content review.

## Findings

### [PAG-001] Human-review flags have no durable disposition record

- Risk: Medium
- Confidence: High
- Status: not evidenced
- Authority: voluntary guidance
- Applicability: extraction lifecycle only; no adverse payer decision is in scope
- Source: NIST AI RMF Core and NAIC Model Bulletin, applicability not established
- Evidence: manifests and UI expose `review_required`, but no authenticated reviewer action or durable system of record exists
- Consequence: a downstream operator cannot prove who reviewed an unresolved extraction
- Minimal remediation: integrate review disposition with the organization’s authenticated system of record
- Verification: trace a flagged segment through reviewer identity, decision, timestamp, and immutable export

This remediation is intentionally not simulated inside the unauthenticated local app.

## Control coverage

| Control | Status | Reason |
|---|---|---|
| Autonomous adverse-decision authority | Out of scope | No decision state or policy engine exists. |
| Individual clinical/coverage criteria | Out of scope | No criteria are applied. |
| Denial reason, notice, and appeal | Out of scope | No denial or notice path exists. |
| Decision timing/public reporting | Out of scope | The app records extraction latency only. |
| Extraction provenance | Observed | Versioned manifests record models, hashes, requests, usage, failures, and review state. |
| PHI/security boundary | Partial | Secure local defaults exist; organizational authorization, contract, retention, and access evidence are external. |
| Validation/lifecycle | Partial | Offline tests and GroundTruth evaluation exist; deployment approval and operating monitoring are not evidenced. |

## Evidence gaps

Likely organizational owners must provide payer/program/jurisdiction applicability, HIPAA role,
BAA/vendor approval, workforce access controls, retention/deletion rules, reviewer qualifications,
monitoring, incident records, and any downstream use of extracted output.

## Sources

Primary authorities are intentionally not asserted as binding because applicability is unknown
and no adverse-decision path exists. Contextual governance sources:

- NIST AI RMF Core: <https://airc.nist.gov/airmf-resources/airmf/5-sec-core/>
- NAIC Model Bulletin on AI Systems by Insurers:
  <https://content.naic.org/sites/default/files/inline-files/2023-12-4%20Model%20Bulletin_Adopted_0.pdf>

