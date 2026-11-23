# Synthetic fixtures

Use committed fixtures as read-only inputs and execute against disposable copies.
Give inputs, independently authored expectations and result evidence separate
paths. Keep personal or organizational data out of the fixtures.

- [Campaigns](campaigns/pass.json), [basic workspace](workspaces/basic/hello.txt)
  and [result envelopes](results/pass.json) exercise Harness execution and contracts.
- [Creation](workspace-create/template.json) and [update](workspace-update/matrix.json)
  provide byte-level inputs for native workspace commands.
- [Ask](ask/source-inputs.json), [Knowledge init](knowledge-init/bootstrap/README.md),
  [Knowledge update](knowledge-update/merge/scenarios.json) and
  [Knowledge curate](knowledge-curate/deduplication/scenarios.json) exercise source,
  evidence and artifact checks.
- [Refine and Implement](refine-implement/cases.json),
  [Review validation](review-validation/cases.json) and
  [single-PR Review](single-pr-review/cases.json) define workflow cases and independent gold.

Read the [validation overview](../docs/validation.md) before changing
expectations or reviewed digests.
