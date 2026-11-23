"""Evaluate the supervised route against fixture gold and independent observations."""
from hashlib import sha256
import json


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def changed_files(before, after):
    left, right = before["entries"], after["entries"]
    return {path for path in left.keys() | right.keys() if left.get(path) != right.get(path)}


def evaluate(case, payload, observations, before, after):
    """Raise on a mismatch. Consume supervisor events, never a self-assigned PASS."""
    mode, expected = case["mode"], case["expect"]
    events = observations["events"]
    require(all(event["sequence"] == index for index, event in enumerate(events, 1)),
            "missing or reordered supervisor event")
    if expected == "BLOCKED" and mode == "probe":
        require(observations["status"] == "BLOCKED" and not events, "unsealed stream reached gate")
    else:
        require(observations["status"] == "OBSERVED", "observation unavailable")
    if events:
        require(all(left["monotonic_ns"] <= right["monotonic_ns"]
                    for left, right in zip(events, events[1:])), "supervisor time reordered")
    require(all(before[key] == after[key] for key in ("root", "branch", "head", "index")),
            "candidate changed repository identity, HEAD or index")

    writes = [event for event in events if event["type"] == "write"]
    decisions = [event for event in events if event["type"] == "checkpoint"]
    report = observations["report"]
    permitted_writes = []
    if mode == "refine":
        contract = observations["contract"]
        source = payload["item"]["source"]
        require(contract and contract["state"] == expected and contract["source"] == source,
                "Refine state or provenance differs")
        text = contract["markdown"]
        require(text.count("Source: " + source + "\n") == 1, "Markdown provenance missing")
        require(text.count("State: " + expected + "\n") == 1, "Markdown state missing")
        if expected == "READY":
            item = payload["item"]
            value = item["target_value"]
            require(f'# {item["id"]} implementation contract\n' in text and "Revision: 1\n" in text,
                    "incorrect contract ID or revision")
            for marker in (f"Requirement R1: set state={value}.",
                           f"Criterion AC1 (R1): exact ASCII state={value} followed by LF.",
                           "Scope: app/settings.txt only;", "Decision: use the value specified by the work item.",
                           f"Baseline: {json.dumps(before, sort_keys=True)}",
                           "Verification V1 (AC1):", "Verification V2 (preservation):"):
                require(marker in text, "incorrect or missing Markdown field " + marker)
        else:
            detail = {"NEEDS_INPUT": "expected value and unit",
                      "BLOCKED": "readable checkout or identified snapshot"}[expected]
            require(contract["detail"] == detail and detail in text, "incorrect resumption condition")
            if expected == "BLOCKED":
                require(observations["source_exists"] is False and payload["baseline"] is None,
                        "BLOCKED lacks independent evidence of missing source")
        if payload["save"]:
            saved_path = "notes/refinements/WI-101.md"
            require(after["entries"].get(saved_path) == {
                "type": "file", "sha256": sha256(text.encode("utf-8")).hexdigest()
            }, "saved Markdown bytes differ")
            permitted_writes.append(saved_path)
        require(not decisions and report is None, "unexpected Implement event")
    elif mode == "implement":
        require(decisions, "missing checkpoint")
        if expected in {"authority", "baseline", "contract"} or case["id"] == "scope":
            require(len(decisions) == 1 and decisions[0]["decision"] == "STOP"
                    and decisions[0]["reason"] == expected, "incorrect stop")
            require(report is None, "report implies work after preflight stop")
        else:
            first = decisions[0]
            require(first["decision"] == "GO" and first["validated"], "GO lacks independent validation")
            permitted_writes.append("app/settings.txt")
            require(writes and first["sequence"] < writes[0]["sequence"], "GO follows the attempt")
            expected_bytes = sha256(b"state=enabled\n").hexdigest()
            require(after["entries"].get("app/settings.txt") == {
                "type": "file", "sha256": expected_bytes}, "wrong target bytes")
            if case["id"] == "late_scope":
                require(len(decisions) == 2 and decisions[1]["decision"] == "STOP"
                        and decisions[1]["reason"] == "scope"
                        and decisions[1]["sequence"] > writes[0]["sequence"], "missing material stop")
                expected_report = "NOT_RUN"
            else:
                require(len(decisions) == 1, "unexpected checkpoint")
                expected_report = "PASS" if expected == "GO" else expected
            verify = [event for event in events if event["type"] == "verification"]
            delivered = [event for event in events if event["type"] == "report"]
            require(len(delivered) == 1, "missing or duplicate report observation")
            if case["id"] == "late_scope":
                require(not verify and decisions[1]["sequence"] < delivered[0]["sequence"],
                        "report precedes the material stop")
            else:
                require(len(verify) == 1 and writes[0]["sequence"] < verify[0]["sequence"] < delivered[0]["sequence"]
                        and writes[0]["completed_ns"] <= verify[0]["monotonic_ns"]
                        and verify[0]["outcome"] == expected_report, "verification did not follow completed write")
            require(report and report["delta"] == permitted_writes
                    and report["verification"] == expected_report
                    and report["observed_verification"] == expected_report
                    and bool(report["residual"]) == (expected_report != "PASS"),
                    "report differs from independent verification")
    elif expected == "deny":
        name = case["id"]
        path, operation = {
            "outside": ("../outside.txt", "write"), "directory": ("unexpected", "mkdir"),
            "metadata": ("app/settings.txt", "metadata"), "index": (".git/index", "write"),
        }.get(name, ("app/settings.txt", "write"))
        require(len(writes) == 1 and writes[0]["outcome"] == "denied"
                and writes[0]["path"] == path and writes[0]["operation"] == operation, "attempt not denied")
        if name in {"early", "child"}:
            require(not decisions, "unexpected GO for early request")
        else:
            require(len(decisions) == 1 and decisions[0]["sequence"] < writes[0]["sequence"]
                    and decisions[0]["validated"] == (name != "invalid_go"), "wrong gate state for denial")
    elif expected == "restore":
        require(len(decisions) == 1 and decisions[0]["validated"] and writes
                and decisions[0]["sequence"] < writes[0]["sequence"], "restore probe skipped checkpoint")
        require([event["after"] for event in writes] == [
            sha256(b"state=enabled\n").hexdigest(), sha256(b"state=disabled\n").hexdigest()
        ], "transient mutation was not observed")
    if mode in {"refine", "implement"}:
        require([event["path"] for event in writes] == permitted_writes
                and all(event["outcome"] == "written" for event in writes), "unexpected write attempt")
        require(changed_files(before, after) == set(permitted_writes), "persistent delta differs")
    else:
        require(not changed_files(before, after), "probe left a persistent delta")
