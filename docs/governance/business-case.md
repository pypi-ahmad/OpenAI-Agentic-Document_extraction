# Healthcare AI governance business case

## Decision requested

Authorize a bounded evidence-discovery phase, not a full governance-program investment. Amount,
owner, decision deadline, funding horizon, and currency are not supplied. Continue only when the
evidence gate below is satisfied.

## Current-state evidence

Repository controls provide strict extraction schemas, quality routing, human-review flags,
provenance, cost/usage capture, security-conscious local defaults, evaluation tooling, and
documented boundaries. Ownership, organizational adoption, reviewer operations, training,
contracts, retention, incident handling, and production monitoring are not evidenced. See the
[control register](control-register.md).

## Economics

Twelve- and 36-month cost, benefit, ROI, and payback are **not calculable**.

```text
expected_loss = event_probability * event_impact
avoided_expected_loss = loss_without_governance - loss_with_governance
incremental_realized_value = forecast_ai_value * governance_attribution_rate
total_benefit = avoided_expected_loss + incremental_realized_value + operating_efficiency
net_benefit = total_benefit - governance_cost
roi = net_benefit / governance_cost
payback_months = governance_cost / recurring_monthly_net_benefit
```

Required inputs are document volume, baseline handling time and labor rate, ADE processing and
review time, material error rates and impacts, adoption forecast, governance attribution,
one-time implementation costs, recurring model/tooling/support costs, and benefit timing. Low,
base, and high scenarios must use defensible organization-specific bounds and avoid overlapping
benefits.

## Risk and value drivers

The evidenced mechanisms are fewer silent extraction failures, reproducible output provenance,
measured model cost, and a defined validation gate. Their financial value is unknown until the
organization measures baseline effort, review burden, error materiality, and adoption.

## Governance-first discovery roadmap

1. Inventory deployments, users, documents, owners, vendors, data, and downstream decisions.
2. Approve data/tool boundaries, acceptable use, exceptions, retention, and reviewer authority.
3. Measure baseline and ADE-assisted time, accuracy, escalations, incidents, and cost.
4. Assign validation, privacy, security, workflow, training, monitoring, and incident owners.
5. Populate 12-/36-month scenarios and fund, change, or stop based on measured evidence.

Each step requires an accountable owner, completion artifact, cost input, and decision gate.

## Assumptions and evidence gaps

The app is treated as extraction-only. No benefit, risk probability, avoided loss, attribution,
budget, compliance status, or benchmark transfer is assumed. Discovery cost is also not
calculable because internal rates and effort estimates were not supplied.

## Evidence ledger

| Claim | Value | Source | Population/period | Role | Limitation |
|---|---|---|---|---|---|
| Extraction records model cost and latency | Qualitative observed control | Repository code/tests | This app, reviewed 2026-09-01 | Direct evidence | Does not establish business benefit. |
| Human-review state is exposed | Qualitative partial control | UI and manifests | This app, reviewed 2026-09-01 | Direct evidence | No authenticated disposition record. |
| ROI/payback | Not calculable | No organization financial evidence supplied | Unknown | Decision constraint | All material inputs are missing. |

No external benchmark is used as a quantitative input.
