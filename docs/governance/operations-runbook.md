# Governance operations runbook

Status: proposed procedure; operational owners are not evidenced.

## Monitor

Track schema-valid pages, failed pages, unresolved segments, routing distribution, retries,
latency, token usage, estimated cost, and GroundTruth accuracy by document class. Aggregate
accuracy must not substitute for material field or subgroup analysis where those populations are
relevant and lawfully available.

## Respond

Stop downstream use when output is structurally invalid, a material identifier is uncertain,
review flags rise unexpectedly, a prompt/profile hash is unknown, or sensitive data may have
been exposed. Preserve non-sensitive operational evidence, avoid copying document content into
logs or tickets, notify the organization’s incident owner, and follow its approved privacy and
security process.

## Recover

Restore the last validated prompt/model/profile combination, rerun offline tests, and perform a
credentialed evaluation only when explicitly authorized. Document the affected population,
period, root cause, remediation, residual risk, and approval to resume.

