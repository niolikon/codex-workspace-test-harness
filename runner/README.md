# Runner entry points

Run the [offline campaign](../GUIDE.md) with `python -B -m runner`.
Contract validation, filesystem boundaries, independent oracles and reporting
are separate modules. `OfflineRunner.run(workspace)` is an internal extension
protocol. The default implementation observes copied synthetic artifacts.

On Windows, see the [Refine and Implement simulator](../docs/refine-implement-windows.md)
for `python -B -m runner.refine_implement_windows` and
[single-PR Review](../docs/single-pr-review.md) for `python -B -m runner.single_pr_review`.
