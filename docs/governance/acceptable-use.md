# Acceptable use and data boundaries

Status: proposed organizational control; adoption and owner are not evidenced.

## Allowed

- Extract documents the operator is authorized to process.
- Review JSON, Markdown, and annotations against the original document.
- Run approved evaluations on non-production or properly authorized reference data.

## Restricted

- Send potentially identifiable health or insurance documents only when the deploying
  organization has approved the OpenAI service, account, region, contract, and data handling.
- Store or share downloaded artifacts only in organization-approved locations.
- Use unresolved output downstream only after qualified human verification.

## Prohibited

- Hardcode or print credentials.
- Use GroundTruth content as production prompt answers or document-specific exceptions.
- Treat extraction output as an autonomous clinical, coverage, payment, fraud, or adverse
  decision.
- Claim compliance, universal accuracy, or LandingAI equivalence without applicable evidence.

## Data handling

Inputs, filenames, rendered pages, outputs, annotations, evaluation artifacts, and manifests may
be sensitive. The app keeps run state in the Streamlit session, but operator downloads and
`evaluation/runs` persist until managed externally. Reset is not a retention or deletion
program. Exceptions require a named owner, purpose, duration, and documented approval outside
this repository.

