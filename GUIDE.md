# Harness guide

Use the Harness to run an offline campaign against synthetic fixtures or to check a local Codex Workspace Template checkout. Start with the example campaign to see what the runner records. Select a Product baseline only when you want to exercise the integration checks.

## Run an offline campaign

Use Python 3.10 or later and Git on your PATH. Run the commands from the Harness repository root. Create `codex-workspace-test-harness-results` next to the repository, for example with `mkdir ../codex-workspace-test-harness-results`.

```text
python -B -m runner --campaign fixtures/campaigns/pass.json --results ../codex-workspace-test-harness-results --validate-only
python -B -m runner --campaign fixtures/campaigns/pass.json --results ../codex-workspace-test-harness-results
```

The first command checks the campaign definition and path boundaries. The second copies the fixtures, runs the checks and produces a verdict. Try the [negative campaign](fixtures/campaigns/fail.json) to see how a mismatch appears in the report. Run the command below to see the expected exit code 1.

```text
python -B -m runner --campaign fixtures/campaigns/fail.json --results ../codex-workspace-test-harness-results
```

The runner reads fixture paths relative to `fixtures/`. You can use the sibling results directory or a descendant as the output root. See the [campaign contract](contracts/campaign.md) to write a campaign of your own.

## Read the evidence

The CLI prints the location of the unique run directory under `runs/`. Validation-only mode reserves no run directory and checks neither fixture availability nor artifact expectations. Read `report.md` for the summary and `result.json` for the checks, findings and provenance. The directory also contains the original campaign input and disposable workspace copies. Compare expected and observed artifacts when a check fails. Reports include the results of handled failures. If execution stops before the reports are written, inspect the artifacts already present and record the interrupted run as incomplete.

| Exit code | Meaning |
| --- | --- |
| 0 | PASS or successful validation-only run |
| 1 | FAIL |
| 2 | Invalid input or path, or an output I/O error |
| 3 | BLOCKED |
| 4 | NOT_RUN or SKIP |

See [result semantics](docs/results.md) for how required and optional checks affect a campaign verdict. Technical completion, evidence integrity, semantic checks, coverage and provenance are evaluated separately. The [case result schema](contracts/case-result.schema.json) describes a different record format from the campaign report.

## Run the self-tests

```text
python -B -m unittest discover -s tests -v
```

Unset `CODEX_PRODUCT_ROOT` before running the standalone suite. Some tests retain disposable artifacts under the sibling results root. Native lifecycle tests use Windows PowerShell and Git for Windows Bash when a Product checkout is selected. Product-dependent cases are reported as unexecuted when their prerequisites are absent. Symlink tests can be skipped if the host lacks the required privilege.

## Validate a Product checkout

Use [Product baseline selection](docs/product-baselines.md) to choose and verify a disposable copy of the intended Product commit. Then set `CODEX_PRODUCT_ROOT` as documented. Keep the real checkout and committed fixtures read-only.

The static checks inspect example paths, distribution contracts and packaging. The native Windows checks invoke the real [creation](docs/windows-creation.md) and [update](docs/windows-update.md) commands using disposable workspaces. They require Windows PowerShell and Git Bash from Git for Windows. The [validation overview](docs/validation.md) links to the Ask, Knowledge, Refine, Implement and Review checks and explains their evidence boundaries.

## Choose workflow checks

Use the [validation overview](docs/validation.md) to choose the check for a workflow. Static checks verify committed Product assets. Synthetic oracles compare authored expectations with fabricated candidates or simulator output. Record missing prerequisites and unexecuted checks with their actual statuses.

See [execution](docs/execution.md) for future runtime adapter requirements.
The maintained checks establish local artifact, process and contract evidence.
Skill selection, model behavior and sandbox enforcement require independent
runtime evidence.
