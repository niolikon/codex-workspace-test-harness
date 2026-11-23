"""P5B3 finite structured fixture oracle, not a runtime document format/parser.

Trusted expectations are independent of candidate plans and self-reports. Exact
fixture facts are decidable here; arbitrary natural language equivalence is not.
"""
import json

from runner.paths import BoundaryError, local, read, snapshot

DIMENSIONS = ("preflight", "completion", "evidence_integrity", "semantic_accuracy",
              "requirement_coverage", "provenance")
PLAN_FIELDS = {"id", "type", "severity", "evidence", "intervention", "documents",
               "preserve", "risks", "authorization", "verification", "disposition"}


def observe(root):
    tree = snapshot(root)
    return {**tree, "timestamps": {p: local(root, p).stat().st_mtime_ns for p in tree["files"]},
            "directory_timestamps": {".": root.stat().st_mtime_ns,
                                     **{p: local(root, p).stat().st_mtime_ns for p in tree["directories"]}}}


def evaluate(candidate, root, expected, before, prewrite):
    issues = []

    def fail(dimension, code):
        item = {"dimension": dimension, "code": code}
        if item not in issues:
            issues.append(item)

    def result(complete=False):
        return {"offline_overall": "FAIL" if issues else "PASS" if complete else "NOT_RUN",
                "runtime_overall": "NOT_RUN",
                "dimensions": {**{d: {"status": "FAIL" if any(i["dimension"] == d for i in issues)
                                          else "PASS" if complete else "NOT_RUN",
                                       "reasons": [i["code"] for i in issues if i["dimension"] == d]}
                                     for d in DIMENSIONS},
                               "tool_use": {"status": "NOT_RUN", "reason": "Fabricated artifacts; no workflow/events"}},
                "issues": issues}

    fields = {"format": str, "scope": str, "outcome": str, "writes": list,
              "plan": list, "mapping": dict, "gaps": list, "verification": list}
    if (not isinstance(candidate, dict) or set(candidate) != set(fields)
            or any(not isinstance(candidate[k], t) for k, t in fields.items())
            or candidate["format"] != "p5b3-synthetic-v1"
            or any(not isinstance(x, str) for k in ("writes", "gaps", "verification") for x in candidate[k])
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in candidate["mapping"].items())
            or any(not isinstance(p, dict) or set(p) != PLAN_FIELDS for p in candidate["plan"])):
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
    try:
        after = observe(root)
    except BoundaryError:
        fail("preflight", "UNSAFE_TREE")
        return result()

    changed = {p for p in set(prewrite["files"]) | set(after["files"])
               if prewrite["files"].get(p) != after["files"].get(p)}
    touched = {p for p in set(prewrite["timestamps"]) & set(after["timestamps"])
               if prewrite["timestamps"][p] != after["timestamps"][p]}
    drift = {p for p in set(before["files"]) | set(prewrite["files"])
             if before["files"].get(p) != prewrite["files"].get(p)
             or before["timestamps"].get(p) != prewrite["timestamps"].get(p)}
    if drift & (changed | touched):
        fail("evidence_integrity", "CONCURRENT_OVERWRITE")
    if touched - changed:
        fail("evidence_integrity", "TIMESTAMP_ONLY")
    if changed - set(expected["write_paths"]):
        fail("evidence_integrity", "UNAUTHORIZED_CHANGE")
    if set(candidate["writes"]) != changed or len(candidate["writes"]) != len(set(candidate["writes"])):
        fail("requirement_coverage", "CHANGE_REPORT")
    if candidate["outcome"] != expected["outcome"]:
        fail("completion", "OUTCOME")
    if candidate["outcome"] == "NO_CHANGE" and (before != after):
        fail("completion", "NO_CHANGE_MODIFIED")
    if candidate["plan"] != expected["plan"]:
        fail("requirement_coverage", "PLAN")
    if candidate["gaps"] != expected["gaps"]:
        fail("requirement_coverage", "GAPS")
    if candidate["verification"] != expected["verification"]:
        fail("requirement_coverage", "VERIFICATION")
    if candidate["mapping"] != expected["mapping"]:
        fail("requirement_coverage", "MAPPING")

    # Exact independent final byte/topology model also protects user notes,
    # unmanaged files, source bytes, classifications and non-claim metadata.
    if after["files"] != expected["final_files"] or after["directories"] != expected["directories"]:
        fail("semantic_accuracy", "FINAL_READBACK")
    for path in expected["protected"]:
        if prewrite["files"].get(path) != after["files"].get(path):
            fail("evidence_integrity", "PROTECTED_CONTENT")
    removed = set(prewrite["files"]) - set(after["files"])
    if removed - set(expected["authorized_deletions"]):
        fail("preflight", "UNAUTHORIZED_DELETE")

    docs = {}
    for path in expected["documents"]:
        target = local(root, path)
        if target.is_file():
            try:
                data = json.loads(read(target))
                if (not isinstance(data, dict) or not isinstance(data.get("claims"), dict)
                        or not isinstance(data.get("links"), list)):
                    raise ValueError("document shape")
                docs[path] = data
            except (ValueError, TypeError):
                fail("semantic_accuracy", "DOCUMENT")

    def resolve(ref):
        path, sep, key = ref.partition("#")
        if not sep:
            return None
        return docs.get(path, {}).get("claims", {}).get(key)

    # Map ALL independently specified original material items to actual surviving
    # claims. Preserve applicability, epistemic kind, confidence and every origin.
    for original, item in expected["preservation"].items():
        value = resolve(candidate["mapping"].get(original, ""))
        if not isinstance(value, dict):
            fail("semantic_accuracy", "PRESERVATION_LOSS")
            continue
        if any(value.get(k) != v for k, v in item.items() if k != "sources"):
            fail("semantic_accuracy", "PRESERVATION_LOSS")
        if not isinstance(value.get("sources"), list) or any(s not in value["sources"] for s in item["sources"]):
            fail("provenance", "PROVENANCE_LOSS")

    # Source authority is bound to independently authored fixture records, never
    # to a candidate-declared revision/digest or a successful read alone.
    for path, doc in docs.items():
        for claim in doc["claims"].values():
            if not isinstance(claim, dict) or not isinstance(claim.get("sources"), list):
                fail("provenance", "SOURCE_FORMAT")
                continue
            for ref in claim["sources"]:
                if not isinstance(ref, dict) or ref not in expected["source_refs"]:
                    fail("provenance", "UNVERIFIABLE_SOURCE")
        if path in expected["check_links"]:
            for link in doc["links"]:
                if not isinstance(link, str) or resolve(link) is None:
                    fail("evidence_integrity", "REFERENCE")
    return result(complete=True)
