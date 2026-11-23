# Knowledge curation validation

```powershell
python -B -m unittest -v tests.test_knowledge_curate tests.test_knowledge_curate_document
```

Select the [Product baseline](product-baselines.md) for package, routing,
contract, example and reviewed-asset checks. The synthetic artifact and
document tests also run without Product.

## Independent finite artifact oracle

`test_knowledge_curate.py` uses versioned expectations, source identities,
original material facts, authorizations, dispositions and expected final hashes
from [curation fixtures](../fixtures/knowledge-curate/deduplication/scenarios.json). Candidates are fabricated artifacts, never outputs
of a workflow invocation. Expected facts and outcomes are not inferred from
candidate self-reports. Positive candidate construction follows authored specs;
negative mutations challenge each preservation/authorization obligation.

The fixture-only JSON knowledge representation makes finite values,
applicability, qualifiers, epistemic kind, confidence and source identities
decidable. It is not a Product format or general Markdown semantics parser.
Exact independent final bytes additionally protect user decisions, coverage,
document classification, unrelated text and source bytes. Every original claim
must map to a surviving actual claim with all relevant origins preserved.
Duplicate equivalence is predefined only for these finite synthetic facts.
Do not claim semantic correctness of arbitrary prose from these checks.

All twenty requested categories have a positive artifact disposition and
negative challenges where a violation applies. Extra cases cover incomplete
queues/mappings, confidence/coverage promotion, source tampering, drift,
insufficient verification, stale knowledge, classification, missing connections
and consolidation retaining discordant historical origins. References resolve
to actual fixture claim anchors. Explicitly blocked original broken references
need not be invented or repaired. Completed repairs and moves check affected
incoming links. Partial failure is a fabricated interrupted artifact state,
not an observed Product failure or execution trace.

Before/prewrite/after observations preserve hashes, topology and file and directory
mtime_ns, including the workspace root. Access times are not compared. Directory
mtimes participate in NO_CHANGE comparisons, not file-only timestamp-write checks.
Timestamp-only violations are rejected by the Knowledge curation evaluator itself. The composed Knowledge update no-change check also verifies directory timestamps. Concurrent edits are constructed before the
prewrite observation; this does not establish protection from real races.

Every execution uses a fresh external disposable tree and retains inputs,
candidate, expectations, observations, dimensions and case records, including
failures. No cleanup occurs. Source fixtures are observed before and after.
A native junction rejection checks that the external sentinel is untouched;
the guard rejects the reparse point before traversal. This proves a local
Harness guard, not runtime model enforcement.

The [document fixture](../fixtures/knowledge-curate/document/a.md) covers
duplicate and distinct origins. Its tests verify complete citation parsing
with either separator, both source paths, support in the actual cited lines
and preservation of separate applicability. Missing, repeated and
staging-only references fail the production citation check.

## Status limits

PASS here means the offline check or intended negative rejection ran and
matched its independent expectation. A rejected candidate records FAIL in its
oracle while the corresponding negative test records PASS. Tool use, skill
selection, model judgment, runtime enforcement and end-to-end acceptance remain
NOT_RUN. Missing prerequisites are BLOCKED; explicit platform/privilege
exclusions are SKIP, never passed coverage. Independent audit remains necessary
before any ACCEPTED_OFFLINE decision.
