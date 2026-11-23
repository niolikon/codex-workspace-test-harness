# Ask validation

Select the [Product baseline](product-baselines.md) to check shipped Ask
instructions and examples. The evidence tests use only synthetic inputs.

```powershell
python -B -m unittest -v tests.test_ask tests.test_ask_evidence
```

The static suite checks skill structure, routed workflow links, examples,
contract copies and the reviewed Product bytes and package inventory in
[the Ask manifest](../fixtures/ask/preserved.json). It removes each guarded
normative clause in memory and checks that the omission is detected. Review
the complete instructions and examples when updating those guards.

The answer oracle uses [synthetic source files](../fixtures/ask/source-inputs.json)
with independently selected TTL and owner claims. It checks claim-specific
citations and separates completion from successful reads. Synthetic event
tests challenge unknown or malformed terminal frames, missing command halves,
failed read commands, mismatched numbered output and reads that occur after
an answer. The trace reader recognizes a narrow event shape. Use
[execution](execution.md) for evidence limits and authorization. Skill loading,
live answer quality and runtime enforcement remain NOT_RUN.
