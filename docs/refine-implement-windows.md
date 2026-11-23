# Refine and Implement Windows simulator qualification

This offline bank exercises Refine and Implement with a trusted simulator. The Product checkout supplies pinned contract bytes for static preservation. The candidate process is a substitute and consumes synthetic work items and snapshots. All authorization in the campaign is simulated. The selected Product and Harness checkouts are read-only during execution.

The Windows Job Object supervises the simulator process tree, bounds streams and waits for drain. The supervisor captures the intent stream before replay. It validates the complete frame schema and sequence and rejects missing, truncated or unusable captures with the mutation gate closed. The external gate validates the checkpoint against the exact simulated contract, observed baseline, scope and authority before executing a mediated write, and records a sequence, monotonic time, path, outcome and resulting hash. This is observation of the mediated route and its request denials. Direct Windows filesystem mutations by a general candidate, process-level attribution of direct write attempts, and native skill or permission enforcement remain outside the qualified route. A child probe supplies an inherited request stream under the same job. The capture does not independently attribute individual requests to a child PID.

Use native Windows PowerShell and the installed Python and Git for Windows. Run from Harness with an existing authorized results directory outside both checkouts.

```powershell
$P = '<absolute path to the selected Product checkout>'
$R = '<absolute path to an existing authorized results directory>'
$env:CODEX_PRODUCT_ROOT = $P
python -B -m runner.refine_implement_windows --results $R --product $P
$env:REFINE_IMPLEMENT_RESULT_ROOT = '<external run directory from the command output>'
python -B -m unittest -v tests.test_refine_implement
```

Each run creates a fresh synthetic Git workspace per case inside its external results directory. Local staged and unstaged sentinels are part of the baseline. The runner preserves failures and performs no cleanup. Read the run summary, per-case snapshots, raw stdout, supervisor events and sealed evidence manifest. Missing completion, a skipped frame and a malformed late frame have expected case status BLOCKED and prove that the mediated gate stays closed. Probes also exercise requests denied after a valid GO and a mutation followed by a restore. Replay tests independently reject modified provenance or values, additional deltas, inconsistent reports, reordered events and verification that precedes the write. The static preservation selector checks the reviewed asset digests and template inventory.

The supervisor qualifies only this explicitly mediated Windows route. Keep actual skill behavior, direct OS attribution and the formal end-to-end handoff outside the resulting PASS claim.
