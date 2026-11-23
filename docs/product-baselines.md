# Select a Product baseline

The release checks target Codex Workspace Template revision
`abc7f9a4c9ac4a07f587daff906b0ac23d557762` and tree
`5ca3c51ee14f7de92e1e452c9c148d9c43afb4cb`. Use a fresh local copy with its
own Git index. Confirm the revision, tree and clean worktree before running
the applicable checks. Keep both canonical repositories read-only.

```powershell
git clone --no-hardlinks --no-checkout -- $ProductSource $ProductCopy
git -C $ProductCopy -c core.autocrlf=false checkout --detach abc7f9a4c9ac4a07f587daff906b0ac23d557762
git -C $ProductCopy rev-parse HEAD 'HEAD^{tree}'
git -C $ProductCopy status --porcelain
$env:CODEX_PRODUCT_ROOT = $ProductCopy
python -B -m unittest -v tests.test_packaging.ProductFoundation
```

Use the public Product repository or a local clone that contains the exact
revision. The native single-PR Review bank requires this release revision and
tree. The Ask, Knowledge, and Refine and Implement preservation fixtures
record the historical revision `e02144b6cee5a28e5f17200cb2489d5b358e4fe9`.
Their static checks verify the release assets against the recorded digests
and template inventories.

Product checks remain NOT_RUN when the Product is unselected. Asset digests
are checked-in expectations. Rebind them only after reviewing an authorized
new Product revision and the functional checks that depend on those bytes.
Use the [validation overview](validation.md) to select individual workflows.
