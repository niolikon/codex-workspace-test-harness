# Result semantics and independent acceptance

| Status | Meaning |
| --- | --- |
| PASS | Applicable required checks ran and established acceptance with evidence |
| FAIL | Executed checks established a violated requirement or integrity condition |
| BLOCKED | A required prerequisite or external condition prevented a meaningful decision |
| NOT_RUN | Planned evaluation was not executed; state why |
| SKIP | Evaluation was deliberately inapplicable or excluded by declared policy; state why |

A skipped optional case must not masquerade as passed coverage. Required blocked or unexecuted checks prevent overall PASS. Known violations take precedence over unknowns: overall FAIL when a required check fails, otherwise BLOCKED for missing prerequisites, otherwise NOT_RUN for unexecuted required checks, otherwise PASS only when all required applicable checks pass. A case with no applicable required checks is SKIP, with rationale. Campaign aggregation must also expose optional outcomes and intended coverage; schema validity alone cannot apply this policy.

## Required independent dimensions

- `preflight`: prerequisites, selected roots and declared controls actually satisfied.
- `completion`: workflow reached its required terminal output rather than just exiting.
- `tool_use`: required tools demonstrably used; claims in prose are insufficient.
- `evidence_integrity`: referenced evidence exists and matches digests and revisions.
- `semantic_accuracy`: conclusions are correct against independent expected facts.
- `requirement_coverage`: each applicable acceptance criterion evaluated, with gaps visible.
- `provenance`: inputs, versions, selections and results have traceable origins.

`overall` records acceptance across these dimensions; requiredness and scenario-specific rules are versioned oracle inputs, not model decisions. Each evaluation records status and reason. PASS should identify supporting evidence; absence of evidence prevents acceptance. Integrity failures produce FAIL even if the agent reports success.

The [envelope schema](../contracts/case-result.schema.json) validates structure only. Offline packaging checks are neither a full JSON Schema engine nor semantic oracles. Independent future tests must include altered evidence, omitted requirements, false success claims and ambiguous or stale revision bindings.

The offline campaign runner implements local artifact oracles and this aggregation policy through a separate [campaign result contract](../contracts/campaign.md). Technical completion, oracle decisions, evidence integrity, coverage and acceptance remain separate. Missing required artifacts are FAIL; unavailable fixtures and faulty oracles are BLOCKED; unexecuted checks stay NOT_RUN. Optional failures and declared exclusions remain visible.
