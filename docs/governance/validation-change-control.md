# Validation and change control

Status: repository control; approving owner and production procedure are not evidenced.

## Change gate

Changes to models, prompts, schemas, rendering, quality scoring, routing thresholds, image
processing, provider configuration, or governance policy must:

1. preserve or intentionally version the output and manifest contracts;
2. pass pytest, Ruff, ty, lock validation, and the local Streamlit smoke test;
3. record prompt, policy, profile, source, page, and artifact hashes;
4. run the curated GroundTruth evaluation only when explicitly authorized;
5. compare accuracy, failures, latency, token use, cost, and review-routing distribution;
6. reject changes that reduce measured quality or hide failures without documented justification.

GroundTruth evaluation is development evidence for the represented sample only. It must not be
described as universal validation.

## Release and rollback evidence

Each production release needs a named approver, change summary, test results, evaluation report
when applicable, known limitations, rollback target, and deployment timestamp. This repository
does not currently provide a release system or durable approval record.

