# Select a Product baseline

The release checks target Codex Workspace Template revision
`e02144b6cee5a28e5f17200cb2489d5b358e4fe9` and tree
`d3bab99c28c6cfe95e065a599beead3c76fa440f`. Use a fresh local copy with its
own Git index. Confirm the revision, tree and clean worktree before running
the applicable checks. Keep both canonical repositories read-only.

```powershell
git clone --no-hardlinks --no-checkout -- $ProductSource $ProductCopy
git -C $ProductCopy -c core.autocrlf=false checkout --detach e02144b6cee5a28e5f17200cb2489d5b358e4fe9
git -C $ProductCopy rev-parse HEAD 'HEAD^{tree}'
git -C $ProductCopy status --porcelain
$env:CODEX_PRODUCT_ROOT = $ProductCopy
python -B -m unittest -v tests.test_packaging.ProductFoundation
```

Use a local source that contains the exact revision. Disabled or unavailable
Product checks remain NOT_RUN when the Product is unselected. Asset digests
are checked-in expectations. Rebind them only after reviewing an authorized
new Product revision and the functional checks that depend on those bytes.
Use the [validation overview](validation.md) to select individual workflows.
