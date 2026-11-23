"""Trusted offline stand-in. Emit requests on stdout and never mutate a fixture."""
import json
from hashlib import sha256
import subprocess
import sys


sequence = 0


def emit(event, **fields):
    global sequence
    sequence += 1
    print(json.dumps({"event": event, "sequence": sequence, **fields}, ensure_ascii=True), flush=True)


def refine(item, baseline, save):
    if baseline is None:
        state, detail = "BLOCKED", "readable checkout or identified snapshot"
    elif item["target_value"] is None:
        state, detail = "NEEDS_INPUT", "expected value and unit"
    else:
        state, detail = "READY", None
    lines = [f'# {item["id"]} implementation contract', "",
             f'Source: {item["source"]}', "Revision: 1", f'State: {state}', ""]
    if state == "READY":
        lines += [f'Requirement R1: set state={item["target_value"]}.',
                  f'Criterion AC1 (R1): exact ASCII state={item["target_value"]} followed by LF.',
                  "Scope: app/settings.txt only; preserve other paths and the index.",
                  "Decision: use the value specified by the work item.",
                  f'Baseline: {json.dumps(baseline, sort_keys=True)}',
                  "Verification V1 (AC1): compare raw bytes.",
                  "Verification V2 (preservation): compare the fixture and Git index."]
    else:
        lines += [f"Resume with: {detail}."]
    markdown = "\n".join(lines) + "\n"
    emit("contract", state=state, source=item["source"], detail=detail, markdown=markdown)
    if save:
        emit("save", path="notes/refinements/WI-101.md")


def implement(payload):
    contract = payload["contract"]
    authority = payload["authority"]
    baseline = payload["baseline"]
    plan = payload["plan"]
    markdown = contract["markdown"]
    if ("Criterion AC1:" not in markdown or
            sha256(markdown.encode("utf-8")).hexdigest() != contract["digest"]):
        reason = "contract"
    elif baseline != contract["baseline"]:
        reason = "baseline"
    elif authority != payload["required_authority"]:
        reason = "authority"
    elif plan != ["app/settings.txt"]:
        reason = "scope"
    else:
        reason = None
    fields = {"revision": contract["revision"], "digest": contract["digest"],
              "baseline": baseline, "authority": authority, "plan": plan}
    if reason:
        emit("checkpoint", decision="STOP", reason=reason, **fields)
        return
    emit("checkpoint", decision="GO", reason="verified", **fields)
    emit("write", path=plan[0], value="state=enabled\n")
    if payload["late_scope"]:
        emit("checkpoint", decision="STOP", reason="scope", **fields)
        emit("report", delta=plan, verification="NOT_RUN", residual="updated scope and authority")
        return
    outcome = payload["verification"]
    emit("report", delta=plan, verification=outcome,
         residual=None if outcome == "PASS" else "AC1 evidence")


def main():
    global sequence
    if sys.argv[1] == "child":
        sequence = int(sys.argv[2])
        emit("write", path="app/settings.txt", value="state=enabled\n")
        return
    payload = json.loads(sys.argv[1])
    action = payload["action"]
    emit("start", actor="simulated")
    if action == "refine":
        refine(payload["item"], payload["baseline"], payload["save"])
    elif action == "implement":
        implement(payload)
    elif action == "child":
        subprocess.run([sys.executable, "-I", "-B", __file__, "child", str(sequence)], check=True, timeout=5)
        sequence += 1
    elif action == "probe":
        for frame in payload["frames"]:
            emit(**frame)
        if payload.get("drop_terminal"):
            return
        if payload.get("drop_middle"):
            sequence += 1
    else:
        raise ValueError(action)
    emit("end")


if __name__ == "__main__":
    main()
