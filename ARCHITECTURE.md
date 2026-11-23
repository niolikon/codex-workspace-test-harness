# Architecture

The Harness validates Codex Workspace Template through separately authored expectations. The offline runner, static Product checks and native Windows suites exercise different boundaries.

## Offline campaign runner

`runner/contract.py` validates bounded JSON input. `runner/paths.py` checks roots, portable names, links and evidence size. `runner/campaign.py` reserves a unique run directory, copies fixtures, invokes `OfflineRunner.run(workspace)` and gathers evidence. `runner/oracles.py` compares the observed artifacts with the declared expectations and aggregates the results.

The default `SnapshotRunner` inspects copied synthetic artifacts. Only the authorized sibling results tree receives run output. See the [campaign contract](contracts/campaign.md) and [result semantics](docs/results.md).

## Product checks

`tests/` contains standalone Harness checks and Product-specific suites. Static checks inspect package structure, examples and contract copies. Native Windows creation and update checks launch the real scripts against synthetic templates and compare the resulting bytes and manifests independently. Follow the [baseline selection guide](docs/product-baselines.md) when running against a specific revision.

The [case-result schema](contracts/case-result.schema.json) describes per-case evidence. The campaign runner uses a separate result format. Local path checks depend on the host filesystem. See [execution](docs/execution.md) and the [validation overview](docs/validation.md) for what each method establishes.
