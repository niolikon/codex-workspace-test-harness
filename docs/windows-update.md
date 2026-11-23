# Native Windows update checks

`tests/test_workspace_update.py` requires explicitly selected `CODEX_PRODUCT_ROOT`, Windows PowerShell 5.1 and actual Git for Windows Bash. Run `python -B -m unittest -v tests.test_workspace_update`. Missing required interpreters are BLOCKED; an unselected Product is NOT_RUN, even if unittest displays a skip marker. There is no inference, remote integration or dependency installation.

The suite uses `fixtures/workspace-update/matrix.json`, copies the Product scripts into a fresh staging directory under TEMP and constructs two synthetic template versions. It does not alter the real Product template or execute committed fixtures in place. The matrix records independently authored expected local bytes and next ownership rows. Python standard-library SHA-256 and exact-byte oracles check all reconciliation cases, schema 1 serialization, source integrity, preview byte/topology/mtime preservation, selective backups, preserved user data, idempotence and PowerShell/Git Bash artifact parity. Workspaces produced by the creation command test actual backward compatibility. Tests do not trust the CLI's own classification or exit code as semantic evidence.

Adversarial checks cover missing/corrupt manifests, duplicate and escaped duplicate properties, schema/structure/hash errors, case/path duplicates, ordering, traversal, reserved/absolute/backslash paths, managed ancestor collisions, unknown options, protected roots and Windows junctions including dangling targets. Optional symbolic links are SKIP when creation privileges are unavailable; no privilege workaround is used.

Native PowerShell debugger breakpoints inject workspace/source/manifest drift, a staged-file move failure after a successful replacement, and a manifest commit failure. For the Git Bash fault cases only, a subprocess-local `exec` function intercepts the final engine launch to attach that debugger. Normal Bash tests exercise unmodified forwarding. Script bytes are unchanged; injected wrappers and competing/moved files remain as evidence. Oracles assert actual resulting bytes, old manifest preservation, retained recovery files and nonzero failure. A throw inside a breakpoint action is not a reliable fault injector, so failures are induced by filesystem conditions instead.

Each invocation has a 45-second timeout and one attempt; timeout terminates the owned Windows process tree. Bounded stdout/stderr, arguments, timeout/exit observations, input script and fixture digests, Git revisions/status and interpreter versions are retained externally. Evidence directories are unique and never automatically removed. The native script tests run separately from synthetic campaigns.

The update command shares a Windows PowerShell engine behind two CLIs. CLI parity does not imply independent Product implementations; oracle independence comes from Python's separately authored expectations. Hostile concurrent races, true disk-full/ACL failures, crash/power-loss durability, Linux, WSL, PowerShell 7 and live Codex integrations remain NOT_RUN.

## Windows staging paths

Use a short local TEMP directory outside the checkouts and the evidence root.
The bank retains staging under a fresh short directory there, with evidence in
the sibling results directory of the Harness checkout. Provenance records both
locations. The bank checks a 240 UTF-16 code unit budget for generated paths,
including the workspace backup GUID and payload suffix. Synthetic paths still
include spaces and Unicode. This bank qualifies ordinary Windows paths.
