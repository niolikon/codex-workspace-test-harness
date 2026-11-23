# Offline campaign contract (version 1)

The executable contract is `runner/contract.py`. Input is UTF-8 JSON, at most 1 MiB. Unknown fields, duplicate keys/identities, nonfinite numbers, unsupported versions, empty collections and unsafe paths are rejected before creating a run. See [positive](../fixtures/campaigns/pass.json) and [negative](../fixtures/campaigns/fail.json) examples.

| Object | Required fields | Optional fields |
| --- | --- | --- |
| Campaign | `contract_version` (integer 1), `campaign_id`, `scenarios` | None |
| Scenario | `id`, `fixture`, boolean `required`, `checks` | `disposition`, `reason` |
| Check | `id`, `kind`, boolean `required` | `path`, `expected`, according to kind |

IDs match `[a-z][a-z0-9-]{0,63}`. Scenario and check collections each contain 1-100 items. Fixture paths are relative to the Harness `fixtures/` root. Oracle paths are relative to the isolated scenario workspace. Portable paths use `/`, letters, digits, `_`, `-` and `.`; absolute paths, empty/dot/traversal components, backslashes, reserved Windows names, trailing dots and links/reparse points are rejected. Trees are limited to 1,000 entries and 8 MiB total, with a 1 MiB per-file limit.

`disposition` defaults to `run`; `skip` declares policy exclusion, `not_run` declares planned unexecuted coverage. Both require a nonempty `reason`; run scenarios cannot include a reason. Exclusions retain every check with its status. Missing fixtures are BLOCKED; missing required artifacts and mismatched expectations are FAIL.

| Oracle kind | Additional properties | Acceptance |
| --- | --- | --- |
| `exists` | `path` | Bounded regular file exists |
| `absent` | `path` | No entry exists |
| `text` | `path`, string `expected` | Exact UTF-8 content including newlines |
| `json` | `path`, JSON `expected` | Canonical JSON equality, sorted keys, scalar types preserved |
| `tree` | `expected`: distinct relative file names | Exact file-name set; directories not compared |
| `unchanged` | None | File digests and directories match initial fixture snapshot |

Expectations come exclusively from the input or immutable source snapshot (`unchanged`). No oracle accepts prose success claims or runner completion alone. Oracle errors are BLOCKED. FAIL takes precedence over BLOCKED and NOT_RUN. Required exclusions are SKIP by declared policy; no applicable required checks means SKIP, never PASS. Optional failures stay visible but do not veto required acceptance. See [status semantics](../docs/results.md).

## Campaign result contract

`result.json` is a separate version-1 campaign record, not the per-case envelope. It includes campaign/run identity, offline mode, technical execution, provenance, scenario records, all five status counts and overall acceptance. Campaign `execution=completed` means all scenarios were processed, including blocked/excluded ones.

Provenance records exact configuration SHA-256, oracle revision, implementation tree SHA-256, Python version and platform. Product is explicitly not consumed. Scenarios record fixture identity/tree SHA-256 (null when unavailable), requiredness, technical state (`not-started`, `running`, `completed`, `failed`), result-relative workspace, checks, evidence, observed tree and all seven case dimensions. Checks include identity, kind, requiredness, status, reason, observations and evidence digests. `tool_use` is SKIP because the offline campaign requires no external tools. Scenario evidence paths are relative to the run; individual check evidence paths are relative to the scenario workspace.

Tree digests hash canonical sorted JSON (`files`: relative-path to SHA-256 map; `directories`: sorted directory list). File digests hash raw bytes. `campaign.json` retains exact selections and expectations. Reports embed no absolute personal paths. Markdown summarizes scenario acceptance and counts; JSON records coverage gaps and errors.

The [case schema](case-result.schema.json) and illustrative fixtures retain their original meaning and bytes. No Product digest is invented. Future live contracts remain in [execution](../docs/execution.md).
