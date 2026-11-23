"""P5B2 fixture-only artifact oracle. No workflow, model or event simulation.

Trusted expectations are versioned independently of candidate reports. BEFORE,
prewrite and AFTER are observations of disposable synthetic trees. They establish
artifact properties, never that a model read a source or enforced a boundary.
"""
import json
from runner.paths import BoundaryError, local, read, sha, snapshot

DIMENSIONS = ("preflight", "completion", "evidence_integrity", "semantic_accuracy",
              "requirement_coverage", "provenance")


def evaluate(candidate, root, expected, before, prewrite):
    issues = []

    def fail(dimension, code):
        entry = {"dimension": dimension, "code": code}
        if entry not in issues:
            issues.append(entry)

    def result(complete=False):
        dimensions = {d: {"status": "FAIL" if any(i["dimension"] == d for i in issues)
                          else "PASS" if complete else "NOT_RUN",
                          "reasons": [i["code"] for i in issues if i["dimension"] == d]}
                      for d in DIMENSIONS}
        dimensions["tool_use"] = {"status": "NOT_RUN", "reason": "No skill or authentic events"}
        return {"offline_overall": "FAIL" if issues else "PASS" if complete else "NOT_RUN",
                "runtime_overall": "NOT_RUN", "dimensions": dimensions, "issues": issues}

    fields = {"format": str, "scope": str, "outcome": str, "writes": list,
              "claims": list, "classifications": dict, "gaps": list,
              "decisions": list, "links": list}
    claim_fields = {"value", "kind", "source", "ref", "revision", "digest"}
    if (not isinstance(candidate, dict) or set(candidate) != set(fields)
            or any(not isinstance(candidate[k], t) for k, t in fields.items())
            or candidate["format"] != "p5b2-synthetic-v1"
            or any(not isinstance(x, str) for k in ("writes", "gaps", "links", "decisions")
                   for x in candidate[k])
            or any(not isinstance(x, dict) or set(x) != claim_fields
                   or any(not isinstance(v, str) for v in x.values()) for x in candidate["claims"])
            or any(not isinstance(k, str) or not isinstance(v, str)
                   for k, v in candidate["classifications"].items())):
        fail("preflight", "FORMAT")
        return result()
    if candidate["scope"] != expected["scope"]:
        fail("preflight", "SCOPE")
    for path in candidate["writes"]:
        try:
            local(root, path)
        except BoundaryError:
            fail("preflight", "UNSAFE_PATH")
        if path not in expected["write_paths"]:
            fail("preflight", "UNAUTHORIZED_WRITE")
    if len(candidate["writes"]) != len(set(candidate["writes"])):
        fail("requirement_coverage", "DUPLICATE_WRITE")
    try:
        after = snapshot(root)
    except BoundaryError:
        fail("preflight", "UNSAFE_TREE")
        return result()

    # Read source bytes independently of any declared candidate digest/revision.
    sources = {}
    for path, spec in expected["sources"].items():
        target = local(root, path)
        if not target.is_file():
            fail("evidence_integrity", "SOURCE_MISSING")
            continue
        data = read(target)
        if sha(data) != spec["digest"]:
            fail("evidence_integrity", "SOURCE_DIGEST")
            continue
        source = json.loads(data)
        if source.get("revision") != spec["revision"] or source.get("facts") != spec["facts"]:
            fail("provenance", "SOURCE_IDENTITY")
            continue
        sources[path] = source

    for claim in candidate["claims"]:
        path = claim["source"]
        try:
            local(root, path)
        except BoundaryError:
            fail("preflight", "UNSAFE_PATH")
        spec = expected["sources"].get(path)
        if spec is None:
            fail("provenance", "SOURCE_UNAVAILABLE")
            continue
        if claim["digest"] != spec["digest"]:
            fail("evidence_integrity", "DECLARED_DIGEST")
        if claim["revision"] != spec["revision"]:
            fail("provenance", "REVISION")
        fact = sources.get(path, {}).get("facts", {}).get(claim["ref"])
        if fact != {"value": claim["value"], "kind": claim["kind"]}:
            fail("semantic_accuracy", "UNSUPPORTED_CLAIM")
    # Independent expected claims also reject cherry-picking a conflicting source.
    if candidate["claims"] != expected["claims"]:
        fail("provenance", "CLAIM_SET")
    if candidate["classifications"] != expected["classifications"]:
        fail("semantic_accuracy", "CLASSIFICATION")
    if candidate["decisions"] != expected["decisions"]:
        fail("provenance", "DECISION")
    if candidate["gaps"] != expected["gaps"]:
        fail("requirement_coverage", "GAPS")
    if candidate["outcome"] != expected["outcome"]:
        fail("completion", "OUTCOME")

    changed = {p for p in set(prewrite["files"]) | set(after["files"])
               if prewrite["files"].get(p) != after["files"].get(p)}
    drift = {p for p in set(before["files"]) | set(prewrite["files"])
             if before["files"].get(p) != prewrite["files"].get(p)}
    if drift & changed:
        fail("evidence_integrity", "CONCURRENT_OVERWRITE")
    for path, code in expected.get("protected_paths", {}).items():
        if prewrite["files"].get(path) != after["files"].get(path):
            fail("semantic_accuracy", code)
    if changed - set(expected["write_paths"]):
        fail("evidence_integrity", "PRESERVATION")
    if set(candidate["writes"]) != changed:
        fail("requirement_coverage", "CHANGE_REPORT")
    if after != expected["final_snapshot"]:
        fail("semantic_accuracy", "FINAL_READBACK")
    if before["directories"] != after["directories"]:
        fail("evidence_integrity", "TOPOLOGY")
    if candidate["outcome"] == "NO_CHANGE" and before != after:
        fail("completion", "NO_CHANGE_MODIFIED")
    if candidate["links"] != expected["links"]:
        fail("evidence_integrity", "LINK_SET")
    for link in candidate["links"]:
        path, _, fragment = link.partition("#")
        try:
            target = local(root, path)
            if path not in expected["sources"] or not target.is_file():
                fail("evidence_integrity", "REFERENCE")
            elif fragment not in sources.get(path, {}).get("facts", {}):
                fail("evidence_integrity", "REFERENCE")
        except BoundaryError:
            fail("preflight", "UNSAFE_PATH")
    return result(complete=True)
