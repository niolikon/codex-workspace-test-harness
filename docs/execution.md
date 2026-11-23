# Execution contract

Run the offline campaign CLI with `python -B -m runner`: see the [guide](../GUIDE.md) and [campaign contract](../contracts/campaign.md). It validates JSON, copies bounded synthetic fixtures into fresh external workspaces, evaluates independent oracles and preserves JSON/Markdown reports. No Codex, subprocess, authentication or network is invoked by this path. Product runtime is not consumed.

The [validation overview](validation.md) lists the maintained static,
synthetic and native Windows checks. The contract below applies to future
runtime adapters. Local path guards and disposable directories do not
establish runtime sandbox enforcement.

## Inputs and preflight

Require a selected Product revision or content digest, fixture identity and digest, scenario and oracle versions, mode, authorized disposable-workspace and external results roots, and finite limits. Resolve roots and reject overlap with either source repository, source fixtures or unrelated user data. Reject path traversal and escaping symlinks. Never repair the user's runtime or overwrite existing results implicitly.

Check tool availability and configured permissions. Offline mode must never invoke Codex or inference. Live mode requires explicit opt-in plus finite wall timeout, idle watchdog, maximum attempts and resource/budget policy. If the runtime cannot enforce a requested limit, report BLOCKED before invocation. No mandatory organizational service or account is permitted.

## Workspace and invocation

Create a fresh disposable workspace per case from immutable synthetic fixtures. Record the exact inputs, invocation arguments with secrets removed, runtime/tool versions, effective restrictions and selected revisions. Direct invocation must use structured arguments and controlled environment/cwd. Offline checks use no nested inference.

Preserve raw Codex JSONL events separately from normalized observations. Event parsing must tolerate unrelated events without fabricating completion. Unknown versions, malformed/truncated streams and missing terminal events must remain explicit limitations. Test against authentic streams before calling the parser stable; synthetic examples cannot establish compatibility.

## Completion and timeout

A future state model distinguishes not-started, preflight, running, completed, failed, timed-out and cancelled observations; these are separate from acceptance statuses. On timeout, stop the owned process tree using supported runtime controls, preserve partial output and identify the last confirmed state. A process exit of zero is only one observation.

Evaluate the [independent dimensions](results.md), preserve evidence and publish a bounded report to the selected external root. Future cleanup is explicit and restricted to owned disposable paths; preserve failure evidence and never clean source data. No automatic retries without declared limits.

## Campaign report

Include campaign/configuration identity, input and oracle revisions, mode and limits, per-case references and statuses, counts by status, incomplete coverage and acceptance decision. Preserve digests and paths relative to the selected result root. Do not expose local personal paths, tokens or unrelated data in shareable reports.
