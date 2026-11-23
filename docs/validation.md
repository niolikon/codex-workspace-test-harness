# Validation by workflow

Start with the [Harness guide](../GUIDE.md) and [Product baseline selection](product-baselines.md).
Use the local tests to compare authored expectations with observed bytes,
checked facts and recorded process outcomes.

| Workflow | Checks |
| --- | --- |
| Harness contracts and offline campaign | Packaging, status envelopes, path guards, isolation, fixture preservation, evidence digests and independent CLI verdicts |
| Workspace creation | [Native Windows creation](windows-creation.md) |
| Workspace update | [Native Windows update](windows-update.md) |
| Ask | [Contract, citations and trace assertions](ask.md) |
| Knowledge | [Init](knowledge-init.md), [update](knowledge-update.md) and [curate](knowledge-curate.md) |
| Refine and Implement | [Windows mediated simulator](refine-implement-windows.md) |
| Review | [Validator and single-PR publication](single-pr-review.md) |

## Interpret results

Read [result semantics](results.md) before reporting an outcome. Report each
applicable check separately. A test can PASS by rejecting a fabricated artifact
whose verdict is FAIL. Missing prerequisites can BLOCK a requested check.
An unselected Product, an unexecuted platform lane and a live check outside the
run remain NOT_RUN. Record intentional exclusions and optional symlink probes
without OS privileges as SKIP.

The case-result schema helper covers the subset used by the shipped envelopes.
It checks structure. Independent artifact oracles check meaning. Reports
should record commands, input revisions and digests, tools, expected outcomes,
actual outcomes and paths to bounded evidence.

## Maintain checks

Keep synthetic inputs independent of candidate output. When Product changes,
review each affected contract, expectation and protected path against the
approved revision before updating its checked-in digest. Add positive and
negative cases for substantive behavior changes. Verify packaging, links,
fixture references and discovery from a fresh checkout before handing off.

Use local synthetic data and disposable copies. Preserve source checks and
failure evidence, including timeout state and available partial output. See
the [execution contract](execution.md) for limits and authorization.
