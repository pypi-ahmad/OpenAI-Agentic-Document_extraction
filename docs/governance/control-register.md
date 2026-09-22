# Governance control register

Assessment date: 2026-09-01  
Scope: this repository and local Streamlit extraction workflow  
Evidence statuses: observed, partial, not evidenced, out of scope

| Domain | Status | Evidence and limitation | Proof needed next |
|---|---|---|---|
| AI system inventory | Partial | One app, one OpenAI provider, GPT-6 Sol models, prompts, and local lifecycle artifacts are inventoried. Business owner, deployment inventory, users, affected population, approval, and retirement status are not evidenced. | Named owner and deployment register. |
| Data classification and tool boundaries | Partial | Inputs are classified as potentially sensitive; official endpoints, `store=False`, loopback binding, ignored secrets, and no result caching are evidenced. Contract/BAA, access enforcement, retention, and approved storage are external. | Privacy/security owner approvals and operating configuration. |
| Acceptable use | Partial | Repository policy defines allowed, restricted, and prohibited uses; UI requires authorization acknowledgement. Organizational adoption, exception handling, and sanctions are not evidenced. | Approved policy and exception owner. |
| Review and validation | Partial | Strict schemas, fail-closed review states, GroundTruth metrics, tests, and change gates are evidenced. Authenticated reviewer approval and production outcome validation are absent. | Reviewer workflow and signed validation record. |
| Accountable ownership and champions | Not evidenced | No accountable business, privacy, security, clinical/workflow, model, or incident owner is named. | RACI or equivalent owner register. |
| Role-appropriate training | Not evidenced | User documentation exists, but required audiences, completion, assessment, and refresh cadence are not evidenced. | Training plan and completion records. |
| Monitoring and audit trail | Partial | Manifests contain hashes, request IDs, attempts, cost, latency, failures, and review state. Durable tamper-resistant storage, alerts, retention, reviewer actions, incidents, complaints, and subgroup monitoring are not evidenced. | Monitoring design, thresholds, durable audit store, and operating records. |

Missing evidence is not proof that a control is absent or that any requirement was violated.
