# Native Windows creation checks

`tests/test_workspace_create.py` invokes native Windows PowerShell and Git Bash using standard-library subprocesses. These checks run separately from the synthetic campaign CLI. No inference or network is used.

Select Product with `CODEX_PRODUCT_ROOT`, optionally select the actual Git for Windows Bash with `CODEX_GIT_BASH`, then run `python -B -m unittest -v tests.test_workspace_create`. Missing Windows/interpreters are BLOCKED prerequisites; an unselected Product is NOT_RUN. A unittest skip marker for an unselected Product must not be interpreted as creation acceptance.

The creation suite copies script bytes and template into unique external `workspace-create-*` campaigns. `fixtures/workspace-create/template.json` supplies bounded synthetic binary/mixed-line-ending/hidden/profile content, Unicode paths and an empty directory. Committed fixtures are never executed or modified in place.

Oracles compare relative topology, every file byte, independent SHA-256 and the exact deterministic manifest. Scenarios cover new/empty destinations, Windows and `/c/...` Git Bash paths, relative paths, spaces, Unicode, no implicit Git/session data, source preservation, missing templates, reserved metadata, files/nonempty destinations, traversal, Windows invalid names, Product/root protection, junctions, previews and controlled copy/manifest failures. PowerShell debugger boundary injection and Bash subprocess-only utility failures leave source script bytes intact and preserve partial artifacts. Optional symlink tests record SKIP when privileges are unavailable. Real disk/ACL/read-denial faults, Linux, WSL, PowerShell 7, live workflows and hostile concurrent mutation remain NOT_RUN.

Additional cases reject dangling junctions, drive-relative paths and injected directory-enumeration failures during both dry-run and creation. An inability to inspect the destination must never become an empty-directory success.

Each invocation records arguments, exit code, timeout state and bounded stdout/stderr. The timeout is 45 seconds with owned Windows process-tree termination and no retry. Input digests, source revisions, interpreter paths/versions, generated workspaces and the independent report are retained externally without automatic cleanup. Final source snapshots protect Product and fixture inputs. No result becomes PASS merely because a command exits zero or emits valid JSON.

Packaging checks validate the creation entry points and use `git ls-files --cached --others --exclude-standard` to exclude ignored local files from versionable source selection.

## Windows invocation evidence

The suite records start, observed progress and finish as JSON Lines for each invocation.
Captured stream bytes and increasing Job Object write bytes can renew
inactivity. Reads, other I/O and process transitions are recorded as telemetry.
A process repeatedly reading its input or using CPU alone can hit the idle
limit. Windows job write accounting can include pipes or writes that do not
change a persistent artifact. The wall limit still applies, and the independent
creation oracles verify the resulting artifacts. The native invocation has a
45-second wall limit, a 15-second inactivity limit and 64 KiB per stream. The Job Object owns
the process tree and the report records the stop reason, drain state and the
digests of the raw bounded streams. The single-PR Review bank uses a separate stream-based watchdog.
