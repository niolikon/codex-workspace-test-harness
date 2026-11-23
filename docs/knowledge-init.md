# Knowledge initialization validation

```powershell
python -B -m unittest -v tests.test_knowledge_init tests.test_knowledge_init_document
```

Select the [Product baseline](product-baselines.md) to check current
instruction and contract bytes, routing and disposable forbidden mutations.
The synthetic tests run without Product. The
[bootstrap fixture](../fixtures/knowledge-init/bootstrap/README.md) checks
initialization with decision and service sources, explicit unknowns, ignored
embedded instructions, preservation of unrelated paths and repeat no-change.

The [document source](../fixtures/knowledge-init/document/spec.md) fixes the
TTL and owner. Document checks challenge summary and fact-local citations,
heading ancestry, incomplete supporting ranges and claims outside the
reviewed source. A citation to a heading alone cannot support the TTL or
owner. Synthetic trace tests distinguish authorized writes, unexpected file
changes, and complete readback after the write.

Use disposable workspaces and retain the selected inputs, expectations,
candidate, independent verdict and bounded snapshots. The synthetic event
shape does not establish runtime tool compatibility. Skill selection, model
behavior and native permission enforcement remain NOT_RUN.
