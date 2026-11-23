"""Native Windows single-PR Review bank and independent acceptance oracle."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import json
import os
import re
import stat
import sys
from threading import Barrier
from time import monotonic
from runner import review_markup as dom, review_validation

ROOT = Path(__file__).resolve().parents[1]

MIB = 1024 * 1024
CASE_SECONDS = 30
CAMPAIGN_SECONDS = 600
DRAIN_SECONDS = 5
EVIDENCE_LIMIT = 256 * MIB
EVIDENCE_FILE_LIMIT = 8 * MIB
INVENTORY_ENTRY_LIMIT = 20000
GOLD_SHA = "74b8be5cf92b1a94181f66e982957fb5e487f25f7f73a0a837feb94bdaaeb12d"
CASES_SHA = "0da761bd47bc79f28a4838e05ddef2956d6a9c2ab5c81fdd82e4ce3d04cc1e60"
SUPERVISOR_SHA = "a2a7745299fe31c83b403ef37cc0803fb3de0aa36e28ab8d887f00ef54ae5689"
DIMENSIONS = ("preflight", "completion", "tool_use", "evidence_integrity",
              "semantic_accuracy", "requirement_coverage", "provenance")
EXIT = {"PASS": 0, "FAIL": 1, "BLOCKED": 2, "NOT_RUN": 2}
require, digest, bounded_read, write = review_validation.require, review_validation.digest, review_validation.bounded_read, review_validation.write_json


def load(raw, *, size_limit=MIB):
    require(0 < len(raw) <= size_limit and not raw.startswith(b"\xef\xbb\xbf"), "invalid JSON framing")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=review_validation.unique_object,
                      parse_int=review_validation.integer, parse_float=review_validation.forbidden_number,
                      parse_constant=review_validation.forbidden_number)


def sealed_inputs():
    fixtures = ROOT / "fixtures/single-pr-review"
    material = bounded_read(fixtures / "cases.json")
    gold = bounded_read(fixtures / "gold.json")
    require(digest(material) == CASES_SHA and digest(gold) == GOLD_SHA, "Single-PR Review input pin mismatch")
    cases, expected = load(material), load(gold)
    ids = [case["id"] for case in cases["cases"]]
    require(len(set(ids)) == len(ids) and set(ids) == set(expected["cases"]),
            "matrix and independent gold disagree")
    require({k: cases["selection"][k] for k in ("work_item", "repository", "pr")} ==
            {k: expected["association"][k] for k in ("work_item", "repository", "pr")},
            "F0 disagrees with independent association")
    for row in expected["cases"].values():
        require(row["diff_opens"] == int(row["acquisition"]["preview"] == "full"),
                "preview diff count and acquisition stage disagree")
    return cases, expected, material, gold


def canonical(path, *, file=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path, "noncanonical path")
    for item in (path, *path.parents):
        require(not review_validation.is_reparse(item.lstat()), f"reparse ancestor: {item}")
    require(path.is_file() if file else path.is_dir(), f"unexpected path type: {path}")
    return path


def file_digest(path, limit, *, single_link=True):
    meta = path.lstat()
    require(stat.S_ISREG(meta.st_mode) and not review_validation.is_reparse(meta) and (not single_link or meta.st_nlink == 1),
            f"unsafe evidence file: {path.name}")
    return digest(bounded_read(path, limit))


def inventory(root, file_limit=MIB, total_limit=EVIDENCE_LIMIT, entry_limit=INVENTORY_ENTRY_LIMIT):
    entries = {}
    total = 0
    pending = [root]
    require(root.is_dir() and not review_validation.is_reparse(root.lstat()), "inventory root")
    while pending:
        for path in pending.pop().iterdir():
            require(len(entries) < entry_limit, "inventory entry budget")
            name = path.relative_to(root).as_posix()
            info = path.lstat()
            require(not review_validation.is_reparse(info), f"reparse in inventory: {name}")
            if stat.S_ISDIR(info.st_mode):
                entries[name] = {"directory": True}
                pending.append(path)
            else:
                total += info.st_size
                require(total <= total_limit, "aggregate byte budget")
                entries[name] = {"bytes": info.st_size, "sha256": file_digest(path, file_limit)}
    return dict(sorted(entries.items()))


def inventory_bytes(entries):
    return sum(entry.get("bytes", 0) for entry in entries.values())


def account_case_evidence(directories, bytes_used, entries_used, *, byte_limit=EVIDENCE_LIMIT):
    """Charge each completed case directory once against the remaining campaign budget."""
    require(0 <= bytes_used <= byte_limit, "aggregate byte budget")
    for directory in directories:
        entries_used += 1  # The directory itself is part of the campaign inventory.
        require(entries_used <= INVENTORY_ENTRY_LIMIT, "inventory entry budget")
        entries = inventory(directory, EVIDENCE_FILE_LIMIT, byte_limit - bytes_used,
                            INVENTORY_ENTRY_LIMIT - entries_used)
        bytes_used += inventory_bytes(entries)
        entries_used += len(entries)
    return bytes_used, entries_used


def seal(root, snapshot_bytes=0):
    body = inventory(root, EVIDENCE_FILE_LIMIT, EVIDENCE_LIMIT + snapshot_bytes)
    target = root / "manifest.sha256.json"
    require(not target.exists(), "evidence manifest already exists")
    write(target, list(body.items()))
    size = target.lstat().st_size
    require(0 < size <= EVIDENCE_FILE_LIMIT, "evidence manifest exceeds file byte limit")
    reread = inventory(root, EVIDENCE_FILE_LIMIT, EVIDENCE_LIMIT + snapshot_bytes)
    manifest = reread.pop(target.name)
    raw = bounded_read(target, EVIDENCE_FILE_LIMIT)
    rows = load(raw, size_limit=EVIDENCE_FILE_LIMIT)
    require(size == len(raw) == manifest["bytes"] and digest(raw) == manifest["sha256"]
            and rows == [[name, details] for name, details in body.items()] and reread == body,
            "evidence manifest reread diverged")
    return manifest["sha256"]


def materialize(directory, selected, variant, slot):
    """Rebind P7's authored facts to this independently selected RV-02 tuple."""
    specs, facts = review_validation.fixture("cases.json"), review_validation.fixture("gold.json")
    kind = "B" if variant in {"contradiction", "false_claim"} else "A"
    manifest, candidate, data = review_validation.material(kind, specs, facts)
    if variant in {"rotate_base", "rotate_head"}:
        changed = variant.split("_")[1]
        data[changed] += b"# RV-02 refreshed snapshot\n"
        for doc in (manifest, candidate):
            row = review_validation.by_id(doc["sources"], changed)
            row.update(bytes=len(data[changed]), sha256=digest(data[changed]))
    target = dict(repository_id=selected["repository"], pr_id=selected["pr"],
                  algorithm=selected["algorithm"], base=selected["base"], head=selected["head"])
    if variant == "missing_coverage":
        review_validation.add_manual(manifest, candidate, "required")
    if variant == "false_claim":
        candidate["results"]["requirements"][0]["status"] = "supported"
    old_header = b"WI-701"
    new_header = selected["work_item"].encode("ascii")
    data["item"] = data["item"].replace(old_header, new_header, 1)
    delta = len(new_header) - len(old_header)
    for offset, citation in zip(facts["offsets"][kind], candidate["citations"]):
        if citation["source_id"] == "item":
            offset[:] = [offset[0] + delta, offset[1] + delta]
            citation["start_byte"], citation["end_byte"] = offset
    paths = {}
    for document in (manifest, candidate):
        document["scope"]["target"] = deepcopy(target)
        document["scope"]["work_item"].update(id=selected["work_item"], sha256=digest(data["item"]))
        document["review_id"] = "rv02-single-pr"
        for source in document["sources"]:
            key = source["id"]
            if key == "item":
                source.update(bytes=len(data[key]), sha256=digest(data[key]), revision=digest(data[key]))
            source["relative_path"] = f'{slot}/{source["relative_path"]}'
            paths[key] = source["relative_path"]
            if key in {"base", "head", "tests"}:
                source["repository_id"] = target["repository_id"]
                source["revision"] = target["base" if key == "base" else "head"]
    require(sum(map(len, data.values())) < 8 * MIB, "source budget")
    for key, raw in data.items():
        path = directory / "sources" / paths[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    write(directory / "manifest.json", manifest)
    candidate["provenance"]["manifest_sha256"] = file_digest(directory / "manifest.json", MIB)
    write(directory / "candidate.json", candidate)
    base_lines = data["base"].splitlines(keepends=True)
    head_lines = data["head"].splitlines(keepends=True)
    hunk = f"@@ -1,{len(base_lines)} +1,{len(head_lines)} @@\n".encode("ascii")
    diff = (b"--- a/export.snapshot\n+++ b/export.snapshot\n" + hunk +
            b"".join(b"-" + line for line in base_lines) + b"".join(b"+" + line for line in head_lines))
    (directory / "selected.diff").write_bytes(diff)
    return manifest, candidate, facts, kind


def check_view(raw, state, source, material, operation):
    manifest, candidate, facts, variant = material
    view = review_validation.load_view(raw)
    require(view.get("state") == state, "review state differs from gold")
    negative_gold = {"missing_coverage": "C-manual-required", "false_claim": "B-false-claim"}
    if operation in negative_gold:
        expectation = facts["case_expectations"][negative_gold[operation]]
    else:
        expectation = facts["case_expectations"]["B" if variant == "B" else "A"]
    review_validation.validation_checks(view, expectation, facts["validator_version"], manifest, candidate)
    if expectation.get("projection"):
        require(view.keys() == review_validation.VIEW_FIELDS, "missing Review v1 projection")
        evidence = review_validation.projection_sources(view, candidate, manifest, source)
        citations = review_validation.projection_citations(view, candidate, evidence, facts, variant)
        review_validation.semantic_results(view, manifest, evidence, citations, facts, variant)
        review_validation.check_coverage(view, manifest, candidate)
        review_validation.check_provenance(view, manifest, candidate, source)
    else:
        require(view.keys() <= review_validation.DIAGNOSTIC_FIELDS, "rejected review contains unbound claims")
        review_validation.check_diagnostic_provenance(view, source)
    return view


def check_preview(preview_raw, expected_fields, view_raw, html_raw):
    preview = load(preview_raw)
    require(set(preview) == set(expected_fields), "preview field set")
    for key, expected in expected_fields.items():
        require(type(preview[key]) is type(expected) and preview[key] == expected,
                f"preview binding: {key}")
    require(len(view_raw) <= MIB and len(html_raw) <= MIB, "preview artefact budget")
    require(dom.extract(html_raw)[0] == dom.expected(view_raw), "preview DOM diverges from view")
    return digest(preview_raw)


def check_pair(workspace, destination, reported, before, after, view_raw, html_raw):
    require(all(after.get(path) == metadata for path, metadata in before.items()),
            "published effect changed preexisting workspace")
    new = after.keys() - before.keys()
    files = {path for path in new if "sha256" in after[path]}
    reviews = {path for path in files if path.endswith("/review.json")}
    require(len(reviews) == 1 and len(files) == 2, "effect is not exactly one pair")
    directory = workspace / next(iter(reviews)).rsplit("/", 1)[0]
    expected_parent = canonical(destination) / "session_data" / "reviews"
    require(str(directory.parent) == str(expected_parent) and
            str(directory.parent.resolve(strict=True)) == str(expected_parent),
            "physical pair is outside the expected destination/session_data/reviews")
    require(reported == str(directory), "reported publication path differs from the physical pair")
    expected = {entry.relative_to(workspace).as_posix() for entry in
                (directory, directory / "review.json", directory / "report.html")}
    require(new == expected, "unexpected stage or workspace effect")
    actual_html, _, _ = dom.pair(directory, view_raw, dom.expected(view_raw))
    require(actual_html == html_raw, "published HTML differs from preview")


def load_observation(path):
    return json.loads(bounded_read(path), object_pairs_hook=review_validation.unique_object)


def check_termination(result, expected_exit):
    require(result["launched"] and result["job_drained"] and result["stop_reason"] is None
            and not result["evidence_redacted"] and not result["stream_limit_reached"],
            f"execution did not drain with complete streams: {result.get('stop_reason')}")
    require(result["exit_code"] in expected_exit, "unexpected technical exit")


TUPLE = ("work_item_id", "repository_id", "pr_id", "algorithm", "base", "head")
CONTEXT = ("session_id", "generation", "action", "destination")
RECEIPT_BINDING = (*TUPLE, *CONTEXT, "review_id", "work_item_sha256",
                   "index_sha256", "manifest_sha256", "preview_id")
RESULT_KEYS = {"phase", "review_state", "gate", "reason", "process", "preview_id",
               "preview_sha256", "published_path"}
REASONS = {
    "select_other_item_pr": {"SELECTION_UNBOUND"},
    "select_other_repo": {"SELECTION_UNBOUND"},
    "omit_selected_pr": {"REQUEST_INVALID"},
    "omit_association": {"INDEX_INVALID", "SELECTION_UNBOUND"},
    "omit_head": {"REQUEST_INVALID"},
    "duplicate_association": {"INDEX_INVALID", "SELECTION_UNBOUND"},
    "foreign_action": {"REQUEST_INVALID", "SCOPE_INVALID"},
    "outside_destination": {"PATH_INVALID", "SCOPE_INVALID"},
    "replace_source_allowlist": {"SCOPE_INVALID", "INPUT_INVALID"},
    "request_other_pr_source": {"SCOPE_INVALID", "INDEX_INVALID", "PATH_INVALID"},
    "no_receipt": {"RECEIPT_ABSENT"},
    "deny_receipt": {"CONSENT_DENIED"},
    "wrong_preview_digest": {"RECEIPT_MISMATCH"},
    "receipt_before_preview": {"RECEIPT_EARLY"},
    "backdate_bound_receipt": {"RECEIPT_EARLY"},
    "duplicate_receipt_field": {"RECEIPT_INVALID"},
    "missing_receipt_field": {"RECEIPT_INVALID"},
    "unknown_receipt_field": {"RECEIPT_INVALID"},
    "rotate_base": {"CONTEXT_CHANGED"},
    "rotate_head": {"CONTEXT_CHANGED"},
    "rotate_session": {"CONTEXT_CHANGED"},
    "resume_session": {"CONTEXT_CHANGED"},
    "replay_after_success": {"RECEIPT_REPLAY"},
    "concurrent_replay": {"RECEIPT_REPLAY"},
    "fail_after_claim": {"RECEIPT_REPLAY"},
    "missing_coverage": {"REVIEW_NOT_COMPLETE"},
    "false_claim": {"REVIEW_NOT_COMPLETE"},
}

SEAM_SCRIPT = r"""
param($Assets,$Assembly,$CaseRoot,$Phase,$RequestSha,$Trace,$PreviewId,$PreviewSha,$ReceiptSha,$Fault)
$ErrorActionPreference = 'Stop'
Add-Type -Path $Assembly
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Record([string]$kind,[string]$path) {
    if (([IO.FileInfo]$Trace).Length -gt 65536) { throw 'probe limit' }
    $event = ConvertTo-Json -Compress -InputObject @{boundary=$kind; name=$path}
    [IO.File]::AppendAllText($Trace,$event + "\x60n",$utf8)
}
$hooks = New-Object Codex.Review.SinglePrReviewHooks
$hooks.BeforeRead = [Action[string,string]] { param($kind,$path) Record $kind $path }
$hooks.AfterClaim = [Action[string]] {
    param($claimPath)
    Record 'claim' $claimPath
    if ($Fault -eq 'yes') { throw 'Harness fault after claim' }
}
$hooks.Writer = [Func[string,string,string,string]] {
    param($viewPath,$workspace,$assetsPath)
    Record 'writer' $viewPath
    return [IO.Path]::Combine($workspace,'harness-writer-double')
}
if ($PreviewId -eq '-') { $PreviewId = $null }
if ($PreviewSha -eq '-') { $PreviewSha = $null }
if ($ReceiptSha -eq '-') { $ReceiptSha = $null }
$code = [Codex.Review.SinglePrReview]::Run(
    $Phase,$CaseRoot,$RequestSha,$PreviewId,$PreviewSha,$ReceiptSha,$Assets,
    [Console]::OpenStandardOutput(),$hooks)
exit $code
""".replace("\\x60", chr(96))


def tuple_fields(selection):
    return dict(zip(TUPLE, (selection["work_item"], selection["repository"], selection["pr"],
                            selection["algorithm"], selection["base"], selection["head"])))


class Trial:
    """Own the fixture, simulator, stage invocations and independent snapshots."""

    def __init__(self, root, assets, launcher, supervisor, deadline, definitions, operation, seam):
        self.root, self.assets, self.launcher = root, assets, launcher
        self.supervisor, self.deadline = supervisor, deadline
        self.operation, self.seam = operation, seam
        self.inputs, self.control = root / "inputs", root / "control"
        self.workspace, self.ledger, self.evidence = (root / p for p in ("workspace", "registry", "evidence"))
        self.reference, self.runlog = root / "reference", root / "observations"
        for folder in (self.inputs, self.control, self.workspace, self.ledger, self.evidence,
                       self.reference, self.runlog, self.workspace / "session_data/reviews"):
            folder.mkdir(parents=True)
        self.cwd = root / "unrelated-cwd"
        self.cwd.mkdir()
        (self.control / "gate.lock").touch(exist_ok=False)
        for folder in (self.workspace, self.workspace / "session_data/reviews", self.cwd):
            (folder / "sentinel.txt").write_bytes(definitions["sentinel"].encode("utf-8"))
        if operation == "fail_after_claim" and not seam:
            self.inject_writer_fault()
        receipt_id = "sentinel"
        write(self.ledger / (digest(receipt_id.encode()) + ".json"),
              {"receipt_id": receipt_id, "preview_id": "f" * 32})
        self.selected = self.record(definitions["selection"], operation)
        self.other = self.record(definitions["alternative"], "none")
        self.unrelated = self.record(definitions["unrelated"], "none")
        cross = dict(definitions["unrelated"], repository="sample/wrong-repo", pr="12")
        cross_repo = self.record(cross, "none", "-cross")[0]
        self.source, self.material = self.selected[1:]
        self.witness = deepcopy(self.selected[0])
        selected, other, unrelated = (item[0] for item in (self.selected, self.other, self.unrelated))
        self.request = dict(schema_version=1, **tuple_fields(definitions["selection"]),
                            session_id=definitions["context"]["session"],
                            generation=definitions["context"]["generation"],
                            action=definitions["context"]["action"], destination="workspace")
        self.index = {"schema_version": 1, "records": [selected, other, unrelated, cross_repo]}
        self.seam_script = self.runlog / "probe.ps1"
        if seam:
            self.seam_script.write_bytes(SEAM_SCRIPT.encode("ascii"))
        if operation == "fail_after_claim" and not seam:
            self.request["destination"] = "workspace/fault"
        self.apply_setup(operation)
        self.pin()
        if operation == "receipt_before_preview":
            self.issue("early", self.receipt_from_expected("0" * 32, "0" * 64))
        self.snapshot("initial")

    def inject_writer_fault(self):
        folder = self.workspace / "fault/session_data/reviews"
        folder.mkdir(parents=True)
        script = self.runlog / "deny-fault-dir.ps1"
        script.write_text("""
param([string]$Target)
$ErrorActionPreference = 'Stop'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = Get-Acl -LiteralPath $Target
$rule = [Security.AccessControl.FileSystemAccessRule]::new($sid,'CreateDirectories','Deny')
$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $Target -AclObject $acl
$set = (Get-Acl -LiteralPath $Target).GetAccessRules($true,$false,[Security.Principal.SecurityIdentifier])
if (!($set | Where-Object {$_.IdentityReference -eq $sid -and $_.AccessControlType -eq 'Deny' -and ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::CreateDirectories)})) { throw 'Writer fault was not installed' }
[Console]::WriteLine('The fixture denies creating directories at the writer destination.')
""", encoding="ascii")
        argv = [str(self.launcher), "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                str(script), "-Target", str(folder)]
        supervised_probe(self.supervisor, self.runlog, "prepare-writer-fault", argv, self.cwd,
                         min(5, self.deadline - monotonic() - DRAIN_SECONDS))

    def record(self, selection, variant, suffix=""):
        from shutil import copyfile
        slot = selection["pr"] + suffix
        source = self.reference / slot
        source.mkdir()
        value = materialize(source, selection, variant, slot)
        record = tuple_fields(selection)
        record["sources"] = []
        for row in value[0]["sources"]:
            rel = row["relative_path"]
            dst = self.inputs / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                require(file_digest(dst, MIB) == row["sha256"], "rebind changed source bytes")
            else:
                copyfile(source / "sources" / rel, dst)
            record["sources"].append(dict(id=row["id"], path=rel, sha256=row["sha256"]))
        for label, name in (("manifest", "manifest.json"), ("candidate", "candidate.json"),
                            ("diff", "selected.diff")):
            dst = self.inputs / slot / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            copyfile(source / name, dst)
            record[label] = {"path": f"{slot}/{name}", "sha256": file_digest(dst, MIB)}
        return record, source, value

    def apply_setup(self, op):
        records = self.index["records"]
        if op == "select_other_item_pr":
            wrong_item_record = records[2]
            for field in ("pr_id", "algorithm", "base", "head"):
                self.request[field] = wrong_item_record[field]
        elif op == "select_other_repo":
            self.request["repository_id"] = records[3]["repository_id"]
        elif op == "omit_selected_pr":
            del self.request["pr_id"]
        elif op == "omit_association":
            records.pop(0)
        elif op == "omit_head":
            del self.request["head"]
        elif op == "duplicate_association":
            records.append(deepcopy(records[0]))
        elif op == "foreign_action":
            self.request["action"] = "publish_remote_review"
        elif op == "outside_destination":
            self.request["destination"] = "unrelated-cwd"
        elif op == "replace_source_allowlist":
            records[0]["sources"] = records[0]["sources"][1:]
        elif op == "request_other_pr_source":
            records[0]["sources"][0]["path"] = records[1]["sources"][0]["path"]

    def pin(self):
        index = self.control / "index.json"
        write(index, self.index)
        self.request["index_sha256"] = file_digest(index, MIB)
        request = self.control / "request.json"
        write(request, self.request)
        self.request_sha = file_digest(request, MIB)
        for label, path, pin in (("request", request, self.request_sha),
                                 ("index", index, self.request["index_sha256"])):
            saved = self.runlog / f"{label}-{pin}.json"
            raw = bounded_read(path)
            if saved.exists():
                require(bounded_read(saved) == raw, f"snapshot changed: {label}")
            else:
                with saved.open("xb") as out:
                    out.write(raw)

    def snapshot(self, label):
        result = {name: inventory(path) for name, path in (
            ("workspace", self.workspace), ("ledger", self.ledger), ("inputs", self.inputs),
            ("control", self.control), ("unrelated", self.cwd))}
        write(self.runlog / f"inventory-{label}.json", result)
        return result

    def invocation(self, label, phase, preview=None, receipt=None, *, fault=False, barrier=None):
        allowed = self.deadline - monotonic() - DRAIN_SECONDS
        require(allowed > 0, "case deadline exhausted before invocation")
        work = self.runlog / label
        work.mkdir()
        trace = work / "probe.jsonl"
        if self.seam:
            trace.touch()
            script = self.seam_script
            flags = ["-Assets", str(self.assets), "-Assembly", str(self.assets.parents[3] / "probe.dll"),
                     "-Trace", str(trace), "-Fault", "yes" if fault else "no"]
            if preview:
                flags += ["-PreviewId", preview["preview_id"], "-PreviewSha", preview["sha"]]
            if receipt is not None:
                flags += ["-ReceiptSha", receipt]
            fixture_flags = ["-RequestSha", self.request_sha]
        else:
            script = self.assets / "invoke-single-pr-review.ps1"
            flags = ["-PreviewId", preview["preview_id"], "-PreviewSha256", preview["sha"]] if preview else []
            if receipt is not None:
                flags += ["-ReceiptSha256", receipt]
            fixture_flags = ["-RequestSha256", self.request_sha]
        argv = [str(self.launcher), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script),
                "-Phase", phase, "-CaseRoot", str(self.root), *fixture_flags, *flags]
        write(work / "command.json", {"argv": argv, "cwd": str(self.cwd), "wall_seconds": int(allowed)})
        if barrier:
            barrier.wait(timeout=min(allowed, 5))
        started = monotonic()
        scratch = work / "tmp"
        scratch.mkdir()
        env = dict(self.supervisor.controlled_env(), TMP=str(scratch), TEMP=str(scratch))
        result, out, err = self.supervisor.run_process(argv, self.cwd, env, allowed, allowed)
        result["monotonic_start_us"] = int(started * 1_000_000)
        result["monotonic_stop_us"] = int(monotonic() * 1_000_000)
        (work / "stdout.bin").write_bytes(out)
        (work / "stderr.bin").write_bytes(err)
        write(work / "observation.json", result)
        if self.seam:
            events = [load(raw) for raw in bounded_read(trace, 65536).splitlines()]
            require(all(set(event) == {"boundary", "name"} and event["boundary"] in
                        {"diff", "source", "candidate", "manifest", "claim", "writer"}
                        for event in events), "unrecognized mediated boundary")
        else:
            events = []
        check_termination(result, {1} if fault else {0})
        parsed = load(out)
        if preview:
            require(parsed["preview_id"] == preview["preview_id"] and parsed["preview_sha256"] == preview["sha"],
                    "action attested different preview")
        require(set(parsed) == RESULT_KEYS and parsed["phase"] == phase, "technical envelope")
        require(parsed["process"] == ("failed" if fault else "completed"), "process result")
        return parsed, events

    def preview_expected(self, ident, state, view_raw, html_raw):
        selected = self.selected[0]
        sources = sorted(selected["sources"], key=lambda row: row["id"])
        return dict(schema_version=1, preview_id=ident, review_id=self.material[0]["review_id"],
                    review_state=state, **{k: self.request[k] for k in (*TUPLE, *CONTEXT)
                                         if k != "destination"},
                    destination=str(self.root / self.request["destination"]), request_sha256=self.request_sha,
                    index_sha256=self.request["index_sha256"],
                    work_item_sha256=self.material[0]["scope"]["work_item"]["sha256"],
                    manifest_sha256=selected["manifest"]["sha256"],
                    candidate_sha256=selected["candidate"]["sha256"], diff_sha256=selected["diff"]["sha256"],
                    sources=sources, view_sha256=digest(view_raw), report_sha256=digest(html_raw))

    def preview(self, label, gold):
        result, events = self.invocation(label, "preview")
        if self.seam:
            check_trace(events, self.inputs, self.witness, gold["acquisition"][label])
        after = self.snapshot(label)
        if gold["review"] is None:
            expect_result(result, gold, self.operation)
            require(result["preview_id"] is None and result["preview_sha256"] is None,
                    "selection denial attested a preview")
            require(not any(self.evidence.iterdir()), "selection denial registered preview or copied inputs")
            return None, after
        require(result["gate"] == "not_evaluated" and result["reason"] == "PREVIEW_READY"
                and result["published_path"] is None and result["review_state"] == gold["review"],
                "preview result")
        ident = result["preview_id"]
        require(isinstance(ident, str) and len(ident) == 32 and
                all(c in "0123456789abcdef" for c in ident), "invalid preview id")
        saved = self.evidence / ident
        view, html, raw = (bounded_read(review_validation.relative_entry(saved, name)) for name in
                           ("view.json", "report.html", "preview.json"))
        check_view(view, gold["review"], self.source, self.material, self.operation)
        expected = self.preview_expected(ident, gold["review"], view, html)
        sha = check_preview(raw, expected, view, html)
        self.preview_observed_us = int(monotonic() * 1_000_000)
        self.preview_mtime_ns = (saved / "preview.json").stat().st_mtime_ns
        write(self.runlog / f"registration-{label}.json", {"observed_monotonic_us": self.preview_observed_us,
              "file_mtime_ns": self.preview_mtime_ns, "preview_id": ident, "sha256": sha})
        require(result["preview_sha256"] == sha, "returned preview digest differs from bytes")
        return dict(expected, sha=sha, view=view, html=html), after

    def receipt_from_expected(self, ident, sha):
        return dict(schema_version=1, receipt_id="r-" + ident[:16], type="simulated", decision="allow",
                    preview_id=ident, preview_sha256=sha,
                    **{k: self.request[k] for k in (*TUPLE, *CONTEXT) if k != "destination"},
                    destination=str(self.root / self.request["destination"]), index_sha256=self.request["index_sha256"],
                    manifest_sha256=self.selected[0]["manifest"]["sha256"],
                    work_item_sha256=self.material[0]["scope"]["work_item"]["sha256"],
                    review_id=self.material[0]["review_id"])

    def issue(self, label, receipt, mutation=None):
        raw = review_validation.json_bytes(receipt)
        if mutation == "duplicate_receipt_field":
            raw = raw.replace(b'{"schema_version":1,', b'{"schema_version":1,"schema_version":1,', 1)
        receipt_path = self.control / "receipt.json"
        receipt_path.write_bytes(raw)
        issued_us = int(monotonic() * 1_000_000)
        if label != "early":
            require(issued_us >= self.preview_observed_us and receipt_path.stat().st_mtime_ns > self.preview_mtime_ns,
                    "simulator did not issue receipt after the observed preview")
        event = {"event": "fixture_receipt_issued", "sha256": digest(raw),
                 "observed_monotonic_us": issued_us}
        if mutation == "backdate_bound_receipt":
            initial = receipt_path.stat()
            requested_mtime = self.preview_mtime_ns - 2_000_000_000
            os.utime(receipt_path, ns=(initial.st_atime_ns, requested_mtime))
            self.backdated_mtime_ns = receipt_path.stat().st_mtime_ns
            require(self.backdated_mtime_ns < self.preview_mtime_ns and
                    bounded_read(receipt_path) == raw, "receipt mtime fixture was not isolated")
            event.update(test="synthetic_early_mtime", initial_mtime_ns=initial.st_mtime_ns,
                         preview_mtime_ns=self.preview_mtime_ns, requested_mtime_ns=requested_mtime)
        event["file_mtime_ns"] = receipt_path.stat().st_mtime_ns
        (self.runlog / f"receipt-{label}.json").write_bytes(raw)
        write(self.runlog / f"simulator-{label}.json", event)
        return file_digest(receipt_path, MIB)

    def receipt(self, preview):
        op = self.operation
        if op == "no_receipt":
            return None
        if op == "receipt_before_preview":
            path = self.control / "receipt.json"
            chronology = load_observation(self.runlog / "simulator-early.json")
            require(chronology["observed_monotonic_us"] < self.preview_observed_us and
                    chronology["file_mtime_ns"] == path.stat().st_mtime_ns < self.preview_mtime_ns,
                    "early fixture receipt chronology")
            return file_digest(path, MIB)
        receipt = self.receipt_from_expected(preview["preview_id"], preview["sha"])
        require(all(receipt[k] == preview[k] for k in RECEIPT_BINDING), "fixture receipt drift")
        if op == "deny_receipt":
            receipt["decision"] = "deny"
        elif op == "wrong_preview_digest":
            receipt["preview_sha256"] = "0" * 64
        elif op == "missing_receipt_field":
            del receipt["head"]
        elif op == "unknown_receipt_field":
            receipt["unknown"] = "synthetic"
        pin = self.issue("initial", receipt, op)
        if op not in {"duplicate_receipt_field", "missing_receipt_field", "unknown_receipt_field",
                      "wrong_preview_digest"}:
            check_receipt(bounded_read(self.control / "receipt.json"), pin, preview)
        return pin

    def rotate(self, operation):
        import _winapi
        # Same file and no-sharing discipline as the native review gate.
        lock = _winapi.CreateFile(str(self.control / "gate.lock"), 0xC0000000, 0, 0, 3, 0, 0)
        try:
            self.rotate_locked(operation)
        finally:
            _winapi.CloseHandle(lock)

    def rotate_locked(self, operation):
        if operation == "rotate_session":
            self.request["session_id"] = "s2"
        if operation in {"rotate_session", "resume_session", "fail_after_claim"}:
            self.request["generation"] += 1
        if operation in {"rotate_base", "rotate_head"}:
            selection = dict(zip(("work_item", "repository", "pr", "algorithm", "base", "head"),
                                 (self.request[k] for k in TUPLE)))
            key = operation.removeprefix("rotate_")
            selection[key] = "snapshot:" + key + "-rotated"
            self.selected = self.record(selection, operation, "-rotated")
            self.source, self.material = self.selected[1:]
            self.witness = deepcopy(self.selected[0])
            self.index["records"][0] = self.selected[0]
            self.request[key] = selection[key]
        if operation == "fail_after_claim" and not self.seam:
            self.request["destination"] = "workspace"
        self.pin()
        write(self.runlog / "external-context-transition.json", {"operation": operation,
              "request_sha256": self.request_sha, "request": self.request})


def check_receipt(raw, pin, preview):
    require(digest(raw) == pin, "receipt pin differs from saved bytes")
    receipt = load(raw)
    require(set(receipt) == {*RECEIPT_BINDING, "schema_version", "receipt_id", "type", "decision",
                             "preview_sha256"}, "receipt field set")
    require(type(receipt["schema_version"]) is int and receipt["schema_version"] == 1
            and receipt["type"] == "simulated" and receipt["decision"] in {"allow", "deny"}
            and isinstance(receipt["receipt_id"], str) and review_validation.ID.fullmatch(receipt["receipt_id"]),
            "receipt shape or explicit decision")
    require(receipt["preview_sha256"] == preview["sha"] and
            all(type(receipt[k]) is type(preview[k]) and receipt[k] == preview[k] for k in RECEIPT_BINDING),
            "receipt binding")
    return receipt


def expect_result(result, gold, operation, *, fault=False, success=False):
    wanted = "allowed" if success or fault else gold["gate"]
    if wanted == "denied" and gold["review"] is None and result.get("phase") == "preview":
        wanted = "not_evaluated"
    reason = ({"WRITER_FAILED", "IO_FAILURE"} if fault else
              {"PUBLISHED"} if wanted == "allowed" else REASONS[operation])
    require(result["gate"] == wanted and result["reason"] in reason,
            f"gate {result['gate']}/{result['reason']} differs from {wanted}/{sorted(reason)}")
    # A pre-validation action denial can have no review_state on its own envelope.
    # The review state was established independently from the saved preview.
    require(result["review_state"] in {None, gold["review"]}, "gate contradicted verified review state")
    if wanted == "allowed" and not fault:
        require(result["review_state"] == gold["review"] and result["published_path"],
                "success lacks review or writer output")
    else:
        require(result["published_path"] is None, "unexpected published path")


def preservation(before, after, *, deny):
    for name in ("inputs", "control", "unrelated"):
        require(before[name] == after[name], f"unexpected {name} drift")
    for name in ("ledger", "workspace"):
        require(all(after[name].get(path) == desc for path, desc in before[name].items()),
                f"{name} preservation drift")
        if deny:
            require(before[name] == after[name], f"E0 {name} inventory changed")


def claim(trial, before, after, preview, pin):
    receipt_raw = bounded_read(trial.control / "receipt.json")
    require(digest(receipt_raw) == pin, "trusted receipt bytes changed")
    receipt = check_receipt(receipt_raw, pin, preview)
    require(receipt["decision"] == "allow", "claim followed a negative decision")
    name = digest(receipt["receipt_id"].encode("utf-8")) + ".json"
    require(after["ledger"].keys() - before["ledger"].keys() == {name}, "claim count or name")
    path = trial.ledger / name
    require(load(bounded_read(path)) ==
            {"receipt_id": receipt["receipt_id"], "preview_id": preview["preview_id"]},
            "claim tuple")
    return path


def check_trace(events, inputs, witness, acquisition, effects=()):
    """Match the fixture's acquisition stage and require its effect boundaries last."""
    prefix_fields = {"none": (), "manifest": ("manifest",),
                     "full": ("manifest", "candidate", "diff")}[acquisition]
    prefix = [(kind, str(inputs / witness[kind]["path"])) for kind in prefix_fields]
    sources = ([] if acquisition != "full" else
               [("source", str(inputs / entry["path"])) for entry in witness["sources"]])
    required_reads = len(prefix) + len(sources)
    observed = [(event["boundary"], event["name"]) for event in events]
    require(observed[:len(prefix)] == prefix and
            Counter(observed[len(prefix):required_reads]) == Counter(sources),
            f"{acquisition} acquisition paths, order or cardinality differ from fixture")
    require(tuple(boundary for boundary, _ in observed[required_reads:]) == effects,
            "effect boundary count or chronology differs from fixture")


def check_step(trial, label, before, preview, pin, result, events, gold, *,
               stage, fault=False, success=False):
    after = trial.snapshot(label)
    preview_dir = trial.evidence / preview["preview_id"]
    for name, wanted in (("preview.json", preview["sha"]), ("view.json", digest(preview["view"])),
                         ("report.html", digest(preview["html"]))):
        require(file_digest(review_validation.relative_entry(preview_dir, name), MIB) == wanted, "registered preview drift")
    expect_result(result, gold, trial.operation, fault=fault, success=success)
    writer = success or (not fault and gold["gate"] == "allowed")
    preservation(before, after, deny=not writer and not fault)
    if trial.seam:
        effects = ("claim", "writer") if writer else ("claim",) if fault else ()
        check_trace(events, trial.inputs, trial.witness, gold["acquisition"][stage], effects)
        if writer:
            view_path = Path(events[-1]["name"])
            require(view_path.is_relative_to(trial.evidence) and
                    bounded_read(view_path) == preview["view"], "writer saw different view bytes")
    if writer or fault:
        claim_path = claim(trial, before, after, preview, pin)
        if trial.seam:
            require(events[-2 if writer else -1]["name"] == str(claim_path), "claim boundary path")
    if trial.operation == "backdate_bound_receipt":
        receipt_path = trial.control / "receipt.json"
        require(receipt_path.stat().st_mtime_ns == trial.backdated_mtime_ns,
                "synthetic receipt mtime changed during action")
    if writer and not trial.seam:
        destination = trial.root / trial.request["destination"]
        check_pair(trial.workspace, destination, result["published_path"], before["workspace"],
                   after["workspace"], preview["view"], preview["html"])
    else:
        require(after["workspace"] == before["workspace"], "unexpected effect on workspace")
    return after


def run_track(trial, gold):
    op = trial.operation
    before = trial.snapshot("before-preview")
    preview, after = trial.preview("preview", gold)
    preservation(before, after, deny=True)
    if preview is None:
        return
    pin = trial.receipt(preview)
    if op.startswith("rotate_") or op == "resume_session":
        trial.rotate(op)
    before = trial.snapshot("before-action")
    if op == "concurrent_replay":
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(trial.invocation, f"racer-{n}", "action", preview, pin,
                                      barrier=barrier) for n in (1, 2)]
            racers = [future.result() for future in futures]
        timing = [load_observation(trial.runlog / f"racer-{n}/observation.json") for n in (1, 2)]
        require(max(t["monotonic_start_us"] for t in timing) < min(t["monotonic_stop_us"] for t in timing),
                "contender invocation windows did not overlap")
        winners = [pair for pair in racers if pair[0]["gate"] == "allowed"]
        losers = [pair for pair in racers if pair[0]["gate"] == "denied"]
        require(len(winners) == len(losers) == 1, "simultaneous replay did not produce one winner")
        expect_result(losers[0][0], gold, op)
        if trial.seam:
            check_trace(losers[0][1], trial.inputs, trial.witness, gold["acquisition"]["contender"])
        before = check_step(trial, "race-effect", before, preview, pin, *winners[0], gold,
                            stage="contender", success=True)
    if op in {"replay_after_success", "fail_after_claim"}:
        fault = op == "fail_after_claim"
        result = trial.invocation("first-action", "action", preview, pin, fault=fault)
        before = check_step(trial, "first-effect", before, preview, pin, *result, gold,
                            stage="first-action", success=not fault, fault=fault)
    result = trial.invocation("action", "action", preview, pin)
    check_step(trial, "action-effect", before, preview, pin, *result, gold, stage="action")
    if gold.get("sequence") in {"recover", "recover_from_fault"}:
        if op == "fail_after_claim":
            trial.rotate(op)
        positive = dict(gold, gate="allowed", e0=False, writer_calls=1)
        next_preview, after_preview = trial.preview("recovery-preview", positive)
        require(next_preview["preview_id"] != preview["preview_id"] and next_preview["sha"] != preview["sha"]
                and next_preview["generation"] >= preview["generation"], "recovery did not renew preview")
        new_receipt = trial.receipt_from_expected(next_preview["preview_id"], next_preview["sha"])
        pin_next = trial.issue("recovery", new_receipt)
        require(pin_next != pin, "recovery reused receipt digest")
        before = trial.snapshot("before-recovery-action")
        preservation(after_preview, {**before, "control": after_preview["control"]}, deny=True)
        result = trial.invocation("recovery-action", "action", next_preview, pin_next)
        check_step(trial, "recovery-effect", before, next_preview, pin_next, *result, positive,
                   stage="recovery-action", success=True)


PRODUCT_BASE = "e02144b6cee5a28e5f17200cb2489d5b358e4fe9"
PRODUCT_TREE = "d3bab99c28c6cfe95e065a599beead3c76fa440f"
ASSETS = "template/.agents/skills/review"
VALIDATOR_ASSETS = ("ReviewValidation.cs", "ReviewRendering.cs", "manifest.schema.json",
             "review.schema.json", "render-review.ps1", "validate-review.ps1", "RENDERING.md")
NEW_ASSETS = ("SinglePrReview.cs", "invoke-single-pr-review.ps1")
HARNESS_FILES = (
    "docs/single-pr-review.md", "fixtures/single-pr-review/cases.json",
    "fixtures/single-pr-review/gold.json", "fixtures/review-validation/cases.json",
    "fixtures/review-validation/gold.json", "runner/single_pr_review.py",
    "runner/review_validation.py", "runner/review_markup.py", "runner/review_windows_job.py",
    "tests/test_packaging.py", "tests/test_single_pr_review.py", "LICENSE",
)
REVIEW_EXAMPLE = "docs/examples/single-pr-review.md"
PRODUCT_FILES = {"docs/review.md", REVIEW_EXAMPLE,
                 "template/.agents/skills/_contracts/review.md", f"{ASSETS}/SKILL.md",
                 *(f"{ASSETS}/{filename}" for filename in NEW_ASSETS)}


def check_review_example(product):
    """Check the shipped example and contract without invoking Product."""
    rel = REVIEW_EXAMPLE
    documents = {name: bounded_read(review_validation.relative_entry(product, name), MIB)
                 for name in sorted(PRODUCT_FILES)}
    require(documents["docs/review.md"] == documents["template/.agents/skills/_contracts/review.md"],
            "Review distribution contract drift")
    text = documents[rel].decode("utf-8")
    for marker in ("# Offline single-PR Review example", "-Phase", "preview", "action",
                   "receipt", "session_data/reviews"):
        require(marker in text, f"Review example missing {marker}")


def supervised_probe(supervisor, run, label, argv, cwd, wall, *, env=None):
    folder = run / label
    folder.mkdir()
    write(folder / "command.json", {"argv": argv, "cwd": str(cwd), "wall_seconds": wall})
    obs, out, err = supervisor.run_process(
        argv, cwd, env or supervisor.controlled_env(), wall, wall)
    (folder / "stdout.bin").write_bytes(out)
    (folder / "stderr.bin").write_bytes(err)
    write(folder / "observation.json", obs)
    check_termination(obs, {0})
    return out, err


def git_observations(git, repo, label):
    """Record independent Git identity, status and index entries for readback."""
    return {
        "identity": git(label + "-identity", repo, "rev-parse", "--show-toplevel", "HEAD", "HEAD^{tree}"),
        "delta": git(label + "-delta", repo, "status", "--porcelain=v1", "--untracked-files=all", "--no-renames", "-z"),
        "index": git(label + "-index", repo, "ls-files", "--stage", "-z"),
    }


def preflight(product, run, powershell, supervisor):
    from shutil import copytree, which
    git = canonical(Path(which("git.exe") or ""), file=True)
    env = supervisor.controlled_env()
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    cwd = run / "preflight-cwd"
    cwd.mkdir()
    pin = {"python": file_digest(Path(sys.executable), 64 * MIB, single_link=False),
           "python_version": sys.version, "powershell": file_digest(powershell, 64 * MIB, single_link=False),
           "git": file_digest(git, 64 * MIB, single_link=False), "supervisor": SUPERVISOR_SHA,
           "cases": CASES_SHA, "gold": GOLD_SHA}

    def probe(label, exe, *args, wall=5, extra=None):
        return supervised_probe(supervisor, run, label, [str(exe), *args], cwd, wall,
                                env=env if extra is None else extra)

    def git_read(label, repo, *args):
        return probe(label, git, "-C", str(repo), "-c", "core.fsmonitor=false", *args)[0]

    def revision(label, repo):
        observations = git_observations(git_read, repo, label)
        path, head, tree = observations["identity"].decode().splitlines()
        require(Path(path) == repo, f"{label} root mismatch")
        rows = observations["delta"].decode("utf-8").split("\0")[:-1]
        if label == "product":
            require((head, tree) == (PRODUCT_BASE, PRODUCT_TREE) and not rows,
                    "Product pin or clean checkout differs")
        else:
            require(all(row[1] == " " for row in rows),
                    "Harness worktree must match the index and contain no untracked files")
        return {"root": str(repo), "head": head, "tree": tree, "delta": rows,
                "index_entries_sha256": digest(observations["index"])}

    pin["product_checkout"] = revision("product", product)
    pin["harness_checkout"] = revision("harness", ROOT)
    pin["git_version"] = probe("git-version", git, "--version")[0].decode().strip()
    shell, _ = probe("powershell-version", powershell, "-NoLogo", "-NoProfile",
                     "-NonInteractive", "-Command", '$PSVersionTable.PSEdition; $PSVersionTable.PSVersion.ToString()')
    edition, version = shell.decode().splitlines()
    require(edition == "Desktop" and version.startswith("5.1."), "native Windows PowerShell 5.1 required")
    pin["powershell_version"] = version
    review_validation.check_review_assets(product)
    pin["validator_assets"] = {}
    for name in VALIDATOR_ASSETS:
        reference = git_read("baseline-" + name, product, "show", f"{PRODUCT_BASE}:{ASSETS}/{name}")
        current = bounded_read(review_validation.relative_entry(product, f"{ASSETS}/{name}"))
        require(current == reference, f"Validator asset byte drift: {name}")
        pin["validator_assets"][name] = digest(reference)
    pin["harness_files"] = {name: file_digest(review_validation.relative_entry(ROOT, name), MIB) for name in HARNESS_FILES}
    pin["product_files"] = {name: file_digest(review_validation.relative_entry(product, name), MIB) for name in sorted(PRODUCT_FILES)}
    original_inventory = inventory(product / "template")
    write(run / "template-source-inventory.json", original_inventory)
    copytree(product / "template", run / "dist")
    require(inventory(run / "dist") == original_inventory, "distribution copy drift")
    compiler = run / "compile-probe.ps1"
    compiler.write_text("""
param([string]$Assets,[string]$Assembly)
$ErrorActionPreference = 'Stop'
Add-Type -Path @("$Assets\\ReviewValidation.cs","$Assets\\ReviewRendering.cs","$Assets\\SinglePrReview.cs") -OutputAssembly $Assembly
""", encoding="ascii")
    scratch = run / "compile-scratch"
    scratch.mkdir()
    probe("compile-probe", powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
          str(compiler), "-Assets", str(run / "dist/.agents/skills/review"),
          "-Assembly", str(run / "probe.dll"), wall=15,
          extra=dict(env, TEMP=str(scratch), TMP=str(scratch)))
    pin["probe_assembly"] = file_digest(run / "probe.dll", MIB)
    remote_markers = re.compile(rb"system\.net|https?://|httpclient|webrequest|invoke-restmethod|"
                                rb"invoke-webrequest|shellExecute|start-process|process\.start|"
                                rb"winsock|winhttp|webclient|\b(?:git|gh|codex)\.exe", re.I)
    inspected = {name: bounded_read(run / "dist/.agents/skills/review" / name) for name in
                 ("ReviewValidation.cs", "ReviewRendering.cs", *NEW_ASSETS)}
    remote_hits = {name: sorted({match.decode("ascii") for match in remote_markers.findall(raw)})
                   for name, raw in inspected.items()}
    write(run / "remote-surface.json", {"checked_assets": {name: digest(raw) for name, raw in inspected.items()},
          "remote_markers": remote_hits, "mediated_adapter_hooks_available": 0,
          "network_capture": "NOT_RUN", "process_environment_keys": sorted(env)})
    require(not any(remote_hits.values()), "candidate has a reference to a remote or process adapter")
    write(run / "pins.json", pin)
    structure_env = dict(env, CODEX_PRODUCT_ROOT=str(product))
    structure_env["PATH"] += os.pathsep + str(git.parent)
    out, err = supervised_probe(supervisor, run, "WS-STRUCT", [
        sys.executable, "-B", "-m", "unittest", "-v", "tests.test_packaging.ProductFoundation",
    ], ROOT, 30, env=structure_env)
    lines = (out + err).splitlines()
    require(sum(line.endswith(b"... ok") for line in lines) == 3
            and any(line.startswith(b"Ran 3 tests") for line in lines), "WS-STRUCT requires three PASS")
    return original_inventory, pin, git, env


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--powershell-exe", type=Path, required=True)
    parser.add_argument("--case", required=True)
    args = parser.parse_args(argv)
    deadline = monotonic() + CAMPAIGN_SECONDS
    try:
        require(os.name == "nt", "Windows is required")
        parent = canonical(args.run_root.parent)
        run = args.run_root
        require(run == parent / run.name and not run.exists(), "run root must be a fresh child")
        product = canonical(args.product_root)
        protected = (ROOT, product, Path.home() / "Projects/codex-workspace-template",
                     Path.home() / "Projects/codex-workspace-test-harness")
        require(not any(run.is_relative_to(path) or path.is_relative_to(run) for path in protected),
                "run root overlaps protected checkout")
        run.mkdir()
    except (OSError, ValueError) as exc:
        print(f"BLOCKED {exc}", file=sys.stderr)
        return EXIT["BLOCKED"]
    summary = {"status": "BLOCKED", "preflight": "BLOCKED", "postflight": "NOT_RUN",
               "first_blocker": None, "case_selector": args.case, "limits": {
                   "case_seconds": CASE_SECONDS, "campaign_seconds": CAMPAIGN_SECONDS,
                   "drain_seconds": DRAIN_SECONDS, "stream_bytes": MIB,
                   "sources_bytes": 8 * MIB, "pair_bytes": 2 * MIB,
                   "evidence_bytes": EVIDENCE_LIMIT, "retries": 0}, "cases": {}}
    snapshot_bytes = 0
    try:
        definitions, gold, cases_raw, gold_raw = sealed_inputs()
        (run / "cases.json").write_bytes(cases_raw)
        (run / "gold.json").write_bytes(gold_raw)
        matrix = definitions["cases"]
        selected = [case for case in matrix if args.case in {"ALL", case["id"]}]
        require(selected, "unknown case selector")
        for case in selected:
            summary["cases"][case["id"]] = {"status": "NOT_RUN", "reason": "Execution has not started."}
        powershell = canonical(args.powershell_exe, file=True)
        supervisor_file = ROOT / "runner/review_windows_job.py"
        require(file_digest(supervisor_file, MIB) == SUPERVISOR_SHA, "frozen supervisor hash")
        from runner import review_windows_job
        baseline, pins, git, git_env = preflight(product, run, powershell, review_windows_job)
        snapshot_bytes = inventory_bytes(baseline)
        initial = inventory(run, EVIDENCE_FILE_LIMIT, EVIDENCE_LIMIT + snapshot_bytes)
        bytes_used = inventory_bytes(initial) - snapshot_bytes
        entries_used = len(initial)
        known_top = {path.name for path in run.iterdir()}
        summary["evidence_accounting"] = {"initial_bytes": bytes_used,
                                          "through_last_case_bytes": bytes_used,
                                          "snapshot_excluded_bytes": snapshot_bytes}
        summary["preflight"] = "PASS"
        summary["status"] = "PASS"
        assets = run / "dist/.agents/skills/review"
        for number, case in enumerate(selected):
            ident = case["id"]
            status = summary["cases"][ident]
            tracks = {track: run / f"{number:02d}-{track}" for track in ("seam", "cli")}
            status["evidence"] = {track: f"{folder.name}/observations" for track, folder in tracks.items()}
            try:
                require(monotonic() < deadline, "campaign deadline exhausted")
                case_deadline = min(deadline, monotonic() + CASE_SECONDS)
                def exercise(track):
                    trial = Trial(tracks[track], assets, powershell,
                                  review_windows_job, case_deadline, definitions,
                                  case["mutation"], track == "seam")
                    try:
                        run_track(trial, gold["cases"][ident])
                    finally:
                        trial.snapshot("terminal")
                with ThreadPoolExecutor(max_workers=2) as workers:
                    phases = {track: workers.submit(exercise, track) for track in tracks}
                    errors = []
                    for track, future in phases.items():
                        try:
                            future.result()
                        except (OSError, ValueError, AssertionError, KeyError, TypeError, RuntimeError) as exc:
                            errors.append(f"{track}: {exc}")
                top = {path.name for path in run.iterdir()}
                new_dirs = [folder for folder in tracks.values() if folder.name in top]
                require(top == known_top | {folder.name for folder in new_dirs},
                        "unexpected top-level evidence change")
                started = monotonic()
                old_bytes = bytes_used
                bytes_used, entries_used = account_case_evidence(new_dirs, bytes_used, entries_used)
                known_top = top
                summary["evidence_accounting"]["through_last_case_bytes"] = bytes_used
                status["evidence_bytes"] = bytes_used - old_bytes
                status["inventory_ms"] = round((monotonic() - started) * 1000)
                require(not errors, " | ".join(errors))
                require(len(new_dirs) == len(tracks), "missing case evidence tree")
                status.update(status="PASS", reason="Both native paths satisfy the independent gold.")
            except (OSError, ValueError, AssertionError, KeyError, TypeError, RuntimeError) as exc:
                status.update(status="FAIL", reason=str(exc))
                summary.update(status="FAIL", first_blocker=f"{ident}: {exc}")
                break
        require(file_digest(run / "probe.dll", MIB) == pins["probe_assembly"], "seam assembly drift")
        require(inventory(product / "template") == inventory(run / "dist") == baseline,
                "Product distribution preservation drift")
        require(all(file_digest(review_validation.relative_entry(ROOT, k), MIB) == v
                    for k, v in pins["harness_files"].items()), "Harness candidate drift")
        require(all(file_digest(review_validation.relative_entry(product, k), MIB) == v
                    for k, v in pins["product_files"].items()), "Product candidate drift")
        def postflight_git(label, repo, *args):
            out, _ = supervised_probe(review_windows_job, run, label,
                [str(git), "-C", str(repo), "-c", "core.fsmonitor=false", *args],
                run / "preflight-cwd", max(0.1, min(5, deadline - monotonic())), env=git_env)
            return out

        for name, repository in (("product", product), ("harness", ROOT)):
            observed = git_observations(postflight_git, repository, name + "-postflight")
            for field, raw in observed.items():
                require(raw == bounded_read(run / f"{name}-{field}/stdout.bin"),
                        f"{name} Git {field} drift")
        summary["postflight"] = "PASS"
    except (OSError, ValueError, AssertionError, KeyError, TypeError, RuntimeError) as exc:
        if summary["preflight"] == "PASS":
            summary["postflight"] = "FAIL"
            summary.update(status="FAIL", first_blocker=str(exc))
        else:
            summary["first_blocker"] = str(exc)
    summary["counts"] = dict(Counter(case["status"] for case in summary["cases"].values()))
    for case in summary["cases"].values():
        case["dimensions"] = {key: {"status": "NOT_RUN", "reason": "Required proof remains incomplete."}
                              for key in DIMENSIONS}
        case["dimensions"]["preflight"] = {"status": summary["preflight"],
                                             "reason": "See campaign pins and preflight commands."}
        if case["status"] == "PASS":
            for key in DIMENSIONS:
                case["dimensions"][key] = {"status": "PASS", "reason": case["reason"]}
        elif case["status"] == "FAIL":
            case["dimensions"]["requirement_coverage"] = {"status": "FAIL", "reason": case["reason"]}
    write(run / "summary.json", summary)
    lines = [f"# Single-PR Review {summary['status']}", "", f"Preflight {summary['preflight']}",
             f"Postflight {summary['postflight']}", ""]
    if summary["first_blocker"]:
        lines += [summary["first_blocker"], ""]
    lines += [f"- {ident} {result['status']} · {result['reason']}"
              for ident, result in summary["cases"].items()]
    (run / "VERDETTO.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        manifest_sha = seal(run, snapshot_bytes)
    except (OSError, ValueError, AssertionError) as exc:
        print(f"FAIL sealing evidence: {exc}", file=sys.stderr)
        return EXIT["FAIL"]
    print(f"{summary['status']} {manifest_sha} {run}")
    return EXIT[summary["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
