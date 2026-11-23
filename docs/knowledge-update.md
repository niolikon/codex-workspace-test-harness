# Knowledge update validation

```powershell
python -B -m unittest -v tests.test_knowledge_update tests.test_knowledge_update_document
```

Select the [Product baseline](product-baselines.md) for skill grammar,
distribution equality and reviewed SHA-256 checks. Appended contradictions
challenge exact-byte checks on disposable copies. Review workflow semantics
against the complete contract when rebinding those hashes.

The [merge fixture](../fixtures/knowledge-update/merge/scenarios.json)
covers conflicting and stale sources, supersession, unrelated data
preservation, provenance, no-change, and partial progress. Fabricated
prewrite drift checks the oracle's rejection of a changed baseline. It
leaves real race protection untested. The document oracle checks quantity,
history, supersession, cited source and exact readback using independent
[baseline](../fixtures/knowledge-update/document/service.md) and
[approved revision](../fixtures/knowledge-update/document/spec-v2.md) bytes.

Each synthetic artifact case keeps before, prewrite and after observations,
a candidate, expectations and an oracle result in a fresh external directory.
Preserve partial evidence on failure. Skill selection, live reconciliation and
runtime authorization remain NOT_RUN.
