# Codex Workspace Test Harness

The Codex Workspace Test Harness checks workspace tooling independently of the code it tests. It runs offline campaigns against synthetic fixtures, compares the resulting files with separately defined expectations, and saves reports you can inspect after each run. It also checks Product packaging and native Windows workflows through separate suites.

You need Python 3.10 or later and Git. From the repository root, try the bundled example.

```text
mkdir ../codex-workspace-test-harness-results
python -B -m runner --campaign fixtures/campaigns/pass.json --results ../codex-workspace-test-harness-results
```

The runner prints the verdict and the directory containing the evidence. The [guide](GUIDE.md) explains how to read the reports, run the self-tests, and validate a local Product checkout. See [LICENSE](LICENSE) for licensing.
