# F02 synthetic fixture v1

This is a test format, not a Knowledge production schema. Expectations were
authored independently of the candidate in positive.json. The source registry
pins real source bytes, identity, synthetic revision, fact symbols and epistemic
kinds. The revisions are fixture labels, not claims about Git history.

The before tree has two inspectable structured sources, an explicitly untrusted
instruction, existing stronger knowledge, unmanaged notes, application code and
an unauthorized sibling. Only subject/knowledge/baseline.md is writable. The
expected final bytes preserve the existing declaration and its provenance.

expectations.json defines authorization, required facts, decision, assumption,
references and final digests. expected-final.md is the independently authored
readback expectation. positive.json is the candidate, not the oracle input that
defines truth. scenarios.json declares each test's expected status and mandatory
rejection codes before evaluation. Its edits and filesystem mutations apply only
to fresh disposable copies. Actual additional rejection reasons are retained.

The fixture has all prerequisites for a full baseline. A missing part fails the
request even if honestly reported as a gap; coverage honesty and workflow outcome
are checked separately. It does not model legitimate unavailable prerequisites
or establish acceptance for arbitrary PARTIAL, NEEDS_INPUT or BLOCKED documents.

Sources use explicit JSON fact symbols. Links use local paths and exact ASCII
level-two headings. No natural-language entailment, general Markdown fragment
resolution, remote identity verification, runtime tool-use or skill behavior is
claimed. Snapshots establish endpoint bytes/topology, not transient accesses.
