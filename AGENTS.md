# Harness invariants

Keep oracles independent of the model's self-report. Never accept exit code zero or structurally valid JSON as sufficient semantic proof. Distinguish preflight, completion, tool use, evidence integrity, semantics, requirements, provenance and overall acceptance.

Protect committed fixtures: use disposable copies for future execution. Record inputs, revisions, configuration and provenance for reproducibility. Preserve bounded evidence, including failed or timed-out execution, without retaining credentials.

Offline checks must not invoke inference or require remote integrations. Live campaigns require explicit opt-in, authorized roots, finite execution/resource limits, timeout and watchdog behavior. Do not run unbounded retries.

Report PASS, FAIL, BLOCKED, NOT_RUN and SKIP accurately under [result semantics](docs/results.md). Do not erase unknowns or upgrade incomplete runs. Read [execution](docs/execution.md) and [validation](docs/validation.md). Changes do not imply permission to commit, publish or mutate external systems.
