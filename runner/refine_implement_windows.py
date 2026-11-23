"""Offline RI-01/02 pilot for the explicitly mediated simulator route."""
import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from time import monotonic_ns
from uuid import uuid4

from . import refine_implement_oracle
from .windows_process import controlled_env, run_process

HARNESS = Path(__file__).resolve().parents[1]
FIXTURES = HARNESS / "fixtures/refine-implement"
SIMULATOR = Path(__file__).with_name("refine_implement_simulator.py")
TARGET = "app/settings.txt"
SAVE = "notes/refinements/WI-101.md"
OLD = "state=disabled\n"
NEW = "state=enabled\n"
MAX_EVENTS = 12


def digest(raw):
    return sha256(raw).hexdigest()


def package(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def linked(path):
    return path.is_symlink() or path.is_junction()


def git(folder, environment, *args):
    return subprocess.run([environment["P6_GIT"], "-C", folder, "--no-optional-locks", *args],
                          env=environment, check=True, capture_output=True, timeout=12).stdout


def snapshot(root, environment):
    result = {}
    for folder, dirs, files in root.walk():
        if folder == root and ".git" in dirs:
            dirs.remove(".git")
        for name in sorted(dirs + files):
            path = folder / name
            if linked(path):
                raise RuntimeError("linked fixture entry")
            rel = path.relative_to(root).as_posix()
            if name in dirs:
                result[rel] = {"type": "directory"}
            elif path.is_file():
                result[rel] = {"type": "file", "sha256": digest(path.read_bytes())}
            else:
                raise RuntimeError("unsupported fixture entry")
    return {"root": str(root), "branch": git(root, environment, "branch", "--show-current").decode().strip(),
            "head": git(root, environment, "rev-parse", "HEAD").decode().strip(),
            "index": digest((root / ".git/index").read_bytes()), "entries": result}


def prepare(root, environment):
    root.mkdir()
    git(root, environment, "init", "--template=", "-b", "main")
    (root / "app").mkdir()
    (root / "notes/refinements").mkdir(parents=True)
    for rel, raw in ((TARGET, OLD.encode("ascii")), ("sentinel.txt", b"preserve\n"), ("local.txt", b"base\n")):
        (root / rel).write_bytes(raw)
    git(root, environment, "add", "--", TARGET, "sentinel.txt", "local.txt")
    git(root, environment, "commit", "--no-verify", "-qm", "Synthetic WI-101 baseline")
    (root / "local.txt").write_bytes(b"staged\n")
    git(root, environment, "add", "--", "local.txt")
    (root / "local.txt").write_bytes(b"working\n")


def input_for(case, items, root, environment, original):
    name, mode = case["id"], case["mode"]
    if mode == "refine":
        item_id = {"needs_input": "WI102", "blocked": "WI103"}.get(name, "WI101")
        return {"action": mode, "item": items[item_id], "save": name == "save",
                "baseline": None if name == "blocked" else original,
                "source_root": str(root / "missing") if name == "blocked" else str(root)}
    if name in {"early", "child", "dropped", "gap"}:
        frames = [{"event": "write", "path": TARGET, "value": NEW}]
        return {"action": mode, "frames": frames, "drop_terminal": name == "dropped",
                "drop_middle": name == "gap"}

    contract = {"id": "WI-101", "revision": 1, "baseline": original, "path": TARGET}
    markdown = ("# WI-101 implementation contract\n\n"
                "Revision: 1\nState: READY\nSource: WI-101@1#export-setting\n"
                "Requirement R1: enable export.\nCriterion AC1: exact ASCII state=enabled plus LF.\n"
                "Scope: app/settings.txt only. Preserve the index and other paths.\n"
                "Decision: use the value supplied by WI-101.\n"
                f'Baseline: {json.dumps(original, sort_keys=True)}\n'
                "Verification V1: compare the setting bytes.\n"
                "Verification V2: compare workspace inventories and Git state.\n")
    if name == "missing_criterion":
        markdown = markdown.replace("Criterion AC1: exact ASCII state=enabled plus LF.\n", "")
    contract["markdown"] = markdown
    contract["digest"] = digest(markdown.encode("utf-8"))
    if name == "contract_digest":
        contract["markdown"] = markdown.replace("enable export.", "enable export now.")
    required = {"actor": "simulated", "revision": contract["revision"], "digest": contract["digest"],
                "root": str(root), "baseline": digest(json.dumps(original, sort_keys=True).encode()),
                "path": TARGET, "operation": "replace"}
    authority = required.copy()
    if name == "authority_absent":
        authority = None
    elif name.startswith("authority_"):
        authority[name.removeprefix("authority_")] = "different"

    if name == "baseline_head":
        (root / "sentinel.txt").write_bytes(b"changed\n")
        git(root, environment, "add", "--", "sentinel.txt")
        git(root, environment, "commit", "--no-verify", "--only", "-qm", "Synthetic drift", "--", "sentinel.txt")
    if name in {"baseline_index", "baseline_worktree"}:
        (root / TARGET).write_bytes(b"drift\n")
        if name == "baseline_index":
            git(root, environment, "add", "--", TARGET)
    plan = [TARGET, "sentinel.txt"] if name == "scope" else [TARGET]
    baseline = snapshot(root, environment)
    payload = {"action": mode, "contract": contract, "authority": authority, "required_authority": required,
               "baseline": baseline, "plan": plan, "late_scope": name == "late_scope",
               "verification": {"verification_fail": "FAIL", "verification_missing": "BLOCKED"}.get(name, "PASS")}
    if mode == "probe":
        checkpoint = {"event": "checkpoint", "decision": "GO", "reason": "probe",
                      "revision": contract["revision"], "digest": contract["digest"],
                      "baseline": baseline, "authority": authority, "plan": plan}
        write = {"event": "write", "path": TARGET, "value": NEW}
        probes = {
            "outside": [dict(write, path="../outside.txt")],
            "directory": [{"event": "mkdir", "path": "unexpected"}],
            "metadata": [{"event": "metadata", "path": TARGET}],
            "index": [dict(write, path=".git/index")],
            "transient": [write, dict(write, value=OLD)],
            "malformed": [write, {"event": "report"}],
            "invalid_go": [write],
        }
        if name == "invalid_go":
            checkpoint["authority"] = dict(authority, revision="different")
        payload["frames"] = [checkpoint, *probes[name]]
    return payload


CAPTURE_PROBES = {"dropped": "missing-terminal", "gap": "sequence", "malformed": "schema"}


def decode_capture(raw, process):
    if (process["exit_code"] != 0 or process["stop_reason"] or not process["job_drained"] or
            process["evidence_redacted"]):
        return None, "transport"
    if not raw.endswith(b"\n"):
        return None, "partial-line"
    try:
        frames = [json.loads(line) for line in raw.splitlines()]
    except (UnicodeError, ValueError):
        return None, "json"
    if not 2 <= len(frames) <= MAX_EVENTS or any(not isinstance(frame, dict) for frame in frames):
        return None, "frame-count-or-type"
    if frames[0].get("event") != "start" or frames[-1].get("event") != "end":
        return None, "missing-terminal"
    if any(type(frame.get("sequence")) is not int or frame["sequence"] != index
           for index, frame in enumerate(frames, 1)):
        return None, "sequence"
    if any(not valid_frame(frame) for frame in frames):
        return None, "schema"
    body = frames[1:-1]
    if (any(frame["event"] in {"start", "end"} for frame in body) or
            any(sum(frame["event"] == event for frame in body) > 1 for event in ("contract", "report"))):
        return None, "envelope"
    return body, None



def valid_frame(frame):
    event = frame.get("event")
    if not isinstance(event, str):
        return False
    fields = {
        "start": {"actor": (str,)}, "end": {},
        "contract": {"state": (str,), "source": (str,), "detail": (str, type(None)), "markdown": (str,)},
        "checkpoint": {"decision": (str,), "reason": (str,), "revision": (int,), "digest": (str,),
                       "baseline": (dict,), "authority": (dict, type(None)), "plan": (list,)},
        "report": {"delta": (list,), "verification": (str,), "residual": (str, type(None))},
        "write": {"path": (str,), "value": (str,)},
        "save": {"path": (str,)}, "mkdir": {"path": (str,)}, "metadata": {"path": (str,)},
    }.get(event)
    return (fields is not None and set(frame) == {"event", "sequence", *fields}
            and all(type(frame[key]) in types for key, types in fields.items())
            and (event != "start" or frame["actor"] == "simulated")
            and (event != "checkpoint" or frame["decision"] in {"GO", "STOP"}))



def observe(root, case, payload, before, expected_file, frames, result):
    """Replay a complete captured intent stream through an independent fail-closed gate."""
    if case["mode"] == "refine":
        result["source_exists"] = Path(payload["source_root"]).exists()
    if frames is None:
        return
    events = result["events"]

    def record(kind, **fields):
        events.append({"type": kind, "sequence": len(events) + 1, "monotonic_ns": monotonic_ns(), **fields})

    allowed = False
    for frame in frames:
        event = frame["event"]
        if event == "contract":
            if result["contract"] is not None:
                raise ValueError("duplicate candidate contract")
            result["contract"] = frame
        elif event == "checkpoint":
            contract = payload["contract"]
            allowed = (frame["decision"] == "GO" and "Criterion AC1:" in contract["markdown"]
                       and digest(contract["markdown"].encode("utf-8")) == contract["digest"]
                       and frame["revision"] == contract["revision"] and frame["digest"] == contract["digest"]
                       and frame["baseline"] == before == contract["baseline"]
                       and frame["authority"] == payload["authority"] == payload["required_authority"]
                       and frame["plan"] == payload["plan"] == [TARGET])
            record("checkpoint", decision=frame["decision"], reason=frame["reason"], validated=allowed)
        elif event in {"write", "save", "mkdir", "metadata"}:
            rel = frame["path"]
            value = frame.get("value")
            valid_save = (event == "save" and rel == SAVE and payload.get("save")
                          and result["contract"] and result["contract"]["state"] == "READY")
            valid_write = (event == "write" and rel == TARGET and
                           allowed and (value == NEW or (case["id"] == "transient" and value == OLD)))
            record("write", path=rel, operation=event, outcome="denied")
            if valid_save or valid_write:
                target = root / rel
                if not target.resolve().is_relative_to(root) or any(
                    linked(item) for item in (target, *target.parents)
                ):
                    raise RuntimeError("unsafe fixture target")
                content = result["contract"]["markdown"] if valid_save else value
                prior = digest(target.read_bytes()) if target.exists() else None
                if valid_save:
                    with target.open("xb") as handle:
                        handle.write(content.encode("utf-8"))
                else:
                    target.write_bytes(content.encode("utf-8"))
                events[-1].update(outcome="written", before=prior, after=digest(target.read_bytes()),
                                  completed_ns=monotonic_ns())
        elif event == "report":
            if result["report"] is not None:
                raise ValueError("duplicate candidate report")
            verification = "NOT_RUN"
            if allowed:
                if not expected_file.is_file():
                    verification = "BLOCKED"
                else:
                    verification = "PASS" if (root / TARGET).read_bytes() == expected_file.read_bytes() else "FAIL"
                record("verification", outcome=verification)
            record("report")
            result["report"] = dict(frame, observed_verification=verification)


def execute(case, folder, items, environment):
    root = folder / "workspace"
    prepare(root, environment)
    original = snapshot(root, environment)
    payload = input_for(case, items, root, environment, original)
    before = snapshot(root, environment)
    expected_file = folder / "expected-setting.bin"
    if case["mode"] == "implement" and case["id"] != "verification_missing":
        expected_file.write_bytes(b"deliberately wrong\n" if case["id"] == "verification_fail" else NEW.encode())
    package(folder / "input.json", payload)
    package(folder / "before.json", before)
    package(folder / "contract-baseline.json", original)
    process, stdout, stderr = run_process(
        [sys.executable, "-I", "-B", SIMULATOR, json.dumps(payload)], folder, controlled_env(), 15, 8)
    (folder / "stdout.jsonl").write_bytes(stdout)
    (folder / "stderr.log").write_bytes(stderr)
    frames, capture_fault = decode_capture(stdout, process)
    expected_fault = CAPTURE_PROBES.get(case["id"])
    observations = {"status": "BLOCKED" if frames is None else "OBSERVED",
                    "capture_fault": capture_fault, "events": [],
                    "contract": None, "report": None}
    after, snapshot_error = None, None
    try:
        try:
            observe(root, case, payload, before, expected_file, frames, observations)
        finally:
            package(folder / "observations.json", observations)
            try:
                after = snapshot(root, environment)
                package(folder / "after.json", after)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                snapshot_error = str(error)
                package(folder / "snapshot-error.json", {"error": snapshot_error})
        if after is None:
            status, reason = "BLOCKED", f"final fixture snapshot unavailable: {snapshot_error}"
        elif frames is None and (expected_fault is None or capture_fault != expected_fault):
            status, reason = "BLOCKED", f"no complete attributable simulator stream: {capture_fault}"
        else:
            refine_implement_oracle.evaluate(case, payload, observations, before, after)
            if expected_fault is not None:
                status, reason = "BLOCKED", f"simulated capture fault {capture_fault}, gate stayed closed"
            else:
                status, reason = "PASS", "independent expected outcome and persistent delta confirmed"
    except (AssertionError, KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        status, reason = "FAIL", str(error)
    return {"case": case["id"], "status": status, "reason": reason, "process": process,
            "qualification_expectation_met": status == "PASS" or (
                case["mode"] == "probe" and reason.startswith("simulated capture fault"))}


def campaign(results, product):
    if os.name != "nt":
        raise RuntimeError("Windows required")
    results_path = results.absolute()
    product_path = product.absolute()
    for path in (results_path, product_path):
        if any(linked(item) for item in (path, *path.parents)):
            raise RuntimeError("linked input root")
    results = results_path.resolve(strict=True)
    product = product_path.resolve(strict=True)
    if any(results.is_relative_to(repo) or repo.is_relative_to(results) for repo in (HARNESS, product)):
        raise RuntimeError("results root overlaps a checkout")
    git_path = shutil.which("git.exe")
    if not git_path:
        raise RuntimeError("Git for Windows required")
    environment = controlled_env()
    environment.update(P6_GIT=git_path, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_OPTIONAL_LOCKS="0", GIT_AUTHOR_NAME="P6 fixture", GIT_COMMITTER_NAME="P6 fixture",
                       GIT_AUTHOR_EMAIL="fixture@example.invalid", GIT_COMMITTER_EMAIL="fixture@example.invalid",
                       GIT_AUTHOR_DATE="2026-11-20T12:00:00+00:00", GIT_COMMITTER_DATE="2026-11-20T12:00:00+00:00")
    for repo in (HARNESS, product):
        actual = Path(git(repo, environment, "rev-parse", "--show-toplevel").decode().strip())
        if actual != repo:
            raise RuntimeError("checkout root mismatch")
    cases_file = FIXTURES / "cases.json"
    work_items = FIXTURES / "work-items.json"
    product_pins = FIXTURES / "preserved.json"
    pins = json.loads(product_pins.read_bytes())["protected_sha256"]
    actual_pins = {rel: digest((product / rel).read_bytes()) for rel in pins}
    if actual_pins != pins:
        raise RuntimeError("Product Refine and Implement asset pins differ")
    run = results / ("refine-implement-" + uuid4().hex)
    run.mkdir()
    proven = [cases_file, work_items, product_pins, SIMULATOR, Path(__file__),
              Path(refine_implement_oracle.__file__), HARNESS / "runner/windows_process.py"]
    package(run / "provenance.json", {
        "mode": "offline-simulated", "observation": "supervised intent replay and persistent snapshots",
        "hardware_os": sys.getwindowsversion().build, "python": sys.version.split()[0],
        "git": git(HARNESS, environment, "--version").decode().strip(), "job_wall_seconds": 15,
        "product_head": git(product, environment, "rev-parse", "HEAD").decode().strip(),
        "harness_head": git(HARNESS, environment, "rev-parse", "HEAD").decode().strip(),
        "harness_inputs": {path.relative_to(HARNESS).as_posix(): digest(path.read_bytes()) for path in proven},
        "product_pins": actual_pins, "full_static_preservation": "separate selector tests.test_refine_implement.RefineImplementPreservation",
    })
    definitions = json.loads(cases_file.read_bytes())
    items = json.loads(work_items.read_bytes())
    summary = []
    for case in definitions["cases"]:
        folder = run / case["id"]
        folder.mkdir()
        try:
            result = execute(case, folder, items, environment)
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            result = {"case": case["id"], "status": "BLOCKED", "reason": str(error),
                      "qualification_expectation_met": False}
        package(folder / "result.json", result)
        summary.append(result)
    unexpected = {case["status"] for case in summary if not case["qualification_expectation_met"]}
    qualification = "FAIL" if "FAIL" in unexpected else "BLOCKED" if unexpected else "PASS"
    report = {"cases": summary, "counts": dict(Counter(case["status"] for case in summary)),
              "simulator_qualification": qualification, "product_behavior": "NOT_RUN",
              "direct_windows_mutation_attribution": "BLOCKED"}
    package(run / "summary.json", report)
    manifest = {}
    for path in sorted(run.rglob("*")):
        if path.is_file() and "workspace" not in path.relative_to(run).parts:
            manifest[path.relative_to(run).as_posix()] = digest(path.read_bytes())
    package(run / "evidence-sha256.json", manifest)
    return run, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--product", type=Path, required=True)
    args = parser.parse_args()
    try:
        run, report = campaign(args.results, args.product)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"run": str(run), "counts": report["counts"],
                      "simulator_qualification": report["simulator_qualification"]}))
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[report["simulator_qualification"]]


if __name__ == "__main__":
    sys.exit(main())
