# Offline Review validation on Windows

Use the [Product baseline](product-baselines.md) for the release example and
native bank. The independent synthetic validator and publication oracles can
also be run without Product.

```powershell
$env:SINGLE_PR_REVIEW_TEST_ROOT = $FreshExternalTestPath
python -B -m unittest -v tests.test_review_validation tests.test_single_pr_review
```

The validator fixtures check positive and rejected projections, source and
citation binding, requirements, guidelines and technical coverage, malformed
JSON, and incomplete process evidence. Set the external test path for
publication and report-sealing tests requiring a disposable directory.

## Native single-PR publication

Run from a clean Harness checkout, or an isolated checkout with the complete
candidate staged and a worktree that matches its index. Keep Product clean at
the pinned revision and tree. The preflight checks package structure,
the sealed case and gold digests, and the Windows job supervisor.

The Product argument points to the repository containing `template/`. The
runner copies that template and calls the distributed entry point from an
unrelated working directory. Use a fresh child of a dedicated external
evidence root.

```powershell
python -B -m runner.single_pr_review `
    --product-root $ProductCopy --run-root $RunDirectory --powershell-exe $PowerShell51 --case ALL
```

Use Windows PowerShell 5.1 Desktop and Python 3.13.2 with the standard library.
`--case` may be a single exact ID from [cases](../fixtures/single-pr-review/cases.json).
The script reports exit 0 for PASS, 1 for FAIL and 2 for BLOCKED. A dependent
case stopped before execution stays NOT_RUN. See [result semantics](results.md).
Product review state, gate decision and technical outcome remain separate.

The [gold](../fixtures/single-pr-review/gold.json) binds the author-selected F0 and expected
outcomes independently of Product. The caller pins the selected records, invokes
preview, checks the exact bytes and issues the synthetic receipt on a separate
channel. The Review validator verifies projected facts, coverage and provenance. The renderer parses
preview HTML and the physical two-file publication. The Harness seam compiles
the same C# gate as the CLI. It records acquisition and claim callbacks and
counts a writer double. For every invocation, the gold specifies the expected
acquisition stage. The oracle checks exact manifest, candidate, diff and source
paths and counts before the claim and writer. The CLI uses the local Review
writer. Its declared path and the inventoried pair must agree under the
fixture's canonical destination at `session_data/reviews/`. Both paths use
separate workspaces under the same case deadline. An external
barrier releases two supervised contenders from independent launch threads.
A fixture directory denies creation of staging directories to exercise the CLI failure path; the seam throws in its
post-claim callback. The old receipt is checked again before recovery.

The 29 cases include two timing probes. `P8-CONSENT-EARLY` issues a receipt before
the simulator can know the preview ID and digest. `P8-CONSENT-EARLY-MTIME` issues
a correctly bound allow receipt after observing the preview, then backdates only
its file modification time. The latter is a synthetic metadata test. It records
the genuine issue order and the modified timestamp, and requires
`RECEIPT_EARLY` and E0.

The bank captures phase argv, working directory, exit, bounded binary streams,
fixture-generated receipts, boundary traces, input/ledger/workspace inventories,
JSON and Markdown results, and a reread SHA-256 manifest. The frozen supervisor
owns Windows job trees. Limits are 30 seconds per row including both paths,
600 seconds campaign, five seconds of drain, 1 MiB per stream or view, 8 MiB
for sources, 2 MiB for the pair and 256 MiB of new evidence (excluding the
template snapshot and external Review replay). An initial inventory accounts for
preflight evidence. Each completed case adds the bytes from its two new
evidence trees to the campaign total. The final manifest inventories the whole
dossier. The bank checks its size against the existing 8 MiB evidence file
limit before rereading its full contents and verifying its SHA-256. The
manifest counts toward the aggregate evidence budget. Automatic retries are zero.

E0 tests the mediated boundaries and persistent local effects. The bank uses
local synthetic input and launches with the frozen supervisor's environment
allowlist. Claims about network behavior outside the exercised code paths
require separate evidence. The bank covers
the local single-PR cases in its sealed catalog. Skill selection, model
behavior and live network behavior require separate evidence.
