"""F02 oracle for the versioned synthetic fixture format only, never Product prose.

The caller supplies trusted expectations and a BEFORE snapshot, not values taken
from the candidate. Actual sources and AFTER bytes are read from a disposable tree.
No model, event parser, production schema or claimed tool execution is involved.
"""
import json
from runner.paths import BoundaryError, local, read, sha, snapshot


DIMENSIONS = ("preflight", "completion", "evidence_integrity",
              "semantic_accuracy", "requirement_coverage", "provenance")


def shape(candidate):
    """Reject unsupported synthetic syntax rather than silently ignoring prose."""
    def record(value, fields):
        return isinstance(value, dict) and set(value) == set(fields) and all(
            isinstance(value[key], kind) for key, kind in fields.items())
    return (record(candidate, {"format": str, "scope": str, "reads": list,
                              "writes": list, "claims": list, "decisions": list,
                              "coverage": dict, "gaps": list, "outcome": str,
                              "links": list})
            and candidate["format"] == "p5b1-synthetic-v1"
            and all(isinstance(x, str) for key in ("reads", "writes", "gaps", "links")
                    for x in candidate[key])
            and all(isinstance(k, str) and isinstance(v, str)
                    for k, v in candidate["coverage"].items())
            and all(record(x, {"key": str, "value": str, "kind": str, "source": str,
                               "revision": str, "digest": str, "ref": str})
                    for x in candidate["claims"] + candidate["decisions"]))


def evaluate(candidate, root, expected, before):
    """Return reason-coded independent dimensions; unexpected errors propagate."""
    issues = []

    def fail(dimension, code):
        item = {"dimension": dimension, "code": code}
        if item not in issues:
            issues.append(item)

    def result(complete=False):
        dimensions = {d: {"status": "FAIL" if any(i["dimension"] == d for i in issues)
                          else ("PASS" if complete else "NOT_RUN"), "reasons": [i["code"] for i in issues
                                                   if i["dimension"] == d]}
                      for d in DIMENSIONS}
        dimensions["tool_use"] = {"status": "NOT_RUN", "reason": "No skill invocation or authentic events"}
        return {"offline_overall": "FAIL" if issues else "PASS", "issues": issues,
                "dimensions": dimensions, "runtime_overall": "NOT_RUN"}

    if not shape(candidate):
        fail("preflight", "FORMAT")
        return result()
    if candidate["scope"] != expected["scope"]:
        fail("preflight", "SCOPE_IDENTITY")
    for key, allowed in (("reads", expected["read_paths"]),
                         ("writes", expected["write_paths"])):
        for value in candidate[key]:
            try:
                local(root, value)
            except BoundaryError:
                fail("preflight", "UNSAFE_PATH")
                continue
            if value not in allowed:
                fail("preflight", "UNAUTHORIZED_" + key.upper())

    # Read sources even if the candidate omits them. Neither candidate digests nor
    # metadata within a source establish expected source identity/revision/facts.
    sources = {}
    for key, spec in expected["sources"].items():
        path = local(root, spec["path"])
        if not path.is_file():
            fail("evidence_integrity", "SOURCE_MISSING")
            continue
        data = read(path)
        if sha(data) != spec["digest"]:
            fail("evidence_integrity", "SOURCE_DIGEST")
            continue
        document = json.loads(data)
        if (document["id"] != key or document["revision"] != spec["revision"]
                or document["facts"] != spec["facts"]):
            fail("provenance", "SOURCE_IDENTITY")
            continue
        sources[key] = document

    supported = set()
    for group in ("claims", "decisions"):
        seen = set()
        for item in candidate[group]:
            key = item["key"]
            spec = expected[group].get(key)
            if key in seen:
                fail("semantic_accuracy", "DUPLICATE_OR_CONTRADICTORY_CLAIM")
            seen.add(key)
            if spec is None:
                fail("semantic_accuracy", "UNSUPPORTED_CLAIM")
                continue
            valid = True
            for field, code, dimension in (
                    ("value", "CLAIM_VALUE", "semantic_accuracy"),
                    ("kind", "EPISTEMIC_KIND", "semantic_accuracy"),
                    ("source", "SOURCE_IDENTITY", "provenance"),
                    ("ref", "REFERENCE_UNRESOLVED", "evidence_integrity")):
                if item[field] != spec[field]:
                    fail(dimension, code)
                    valid = False
            source_spec = expected["sources"][spec["source"]]
            if item["revision"] != source_spec["revision"]:
                fail("provenance", "REVISION")
                valid = False
            if item["digest"] != source_spec["digest"]:
                fail("evidence_integrity", "DECLARED_DIGEST")
                valid = False
            source = sources.get(spec["source"])
            fact = source["facts"].get(item["ref"]) if source else None
            if fact != {"value": item["value"], "kind": item["kind"]}:
                fail("semantic_accuracy", "SOURCE_SUPPORT")
                valid = False
            if valid:
                supported.add(key)

    missing = sorted(set(expected["required"]) - supported)
    if missing:
        # This fixture supplies every prerequisite for the requested full baseline.
        # Honest gap reporting is checked separately from satisfying that request.
        fail("requirement_coverage", "REQUIRED_EVIDENCE_MISSING")
    expected_coverage = {key: "supported" if key in supported else "missing"
                         for key in expected["required"]}
    if candidate["coverage"] != expected_coverage or sorted(candidate["gaps"]) != missing:
        fail("requirement_coverage", "COVERAGE")
    missing_decisions = set(expected["decisions"]) - supported
    if missing_decisions:
        if candidate["outcome"] != "NEEDS_INPUT":
            fail("completion", "MISSING_DECISION")
    elif missing:
        if candidate["outcome"] != "PARTIAL":
            fail("completion", "INCOMPLETE")
    elif candidate["outcome"] not in ("COMPLETED", "NO_CHANGE"):
        fail("completion", "OUTCOME")
    if candidate["outcome"] == "COMPLETED" and missing:
        fail("completion", "UNJUSTIFIED_COMPLETED")

    try:
        after = snapshot(root)
    except BoundaryError:
        fail("preflight", "UNSAFE_TREE")
        return result()
    changed = {key for key in set(before["files"]) | set(after["files"])
               if before["files"].get(key) != after["files"].get(key)}
    if changed - set(expected["write_paths"]):
        fail("evidence_integrity", "PRESERVATION")
    if set(before["directories"]) != set(after["directories"]):
        fail("evidence_integrity", "TOPOLOGY")
    if set(candidate["writes"]) != changed:
        fail("requirement_coverage", "CHANGE_REPORT")
    for key, digest in expected["final_files"].items():
        if after["files"].get(key) != digest:
            fail("semantic_accuracy", "FINAL_READBACK")
    if candidate["outcome"] == "NO_CHANGE" and before != after:
        fail("completion", "NO_CHANGE_MODIFIED")

    # Fixture-only local Markdown links: exact lowercase ASCII headings, no
    # remote links, generated slugs, HTML anchors or generic Markdown parser.
    if set(candidate["links"]) != set(expected["links"]):
        fail("evidence_integrity", "LINK_SET")
    for link in candidate["links"]:
        path, separator, fragment = link.partition("#")
        try:
            target = local(root, path)
            if path not in expected["link_paths"] or not target.is_file():
                fail("evidence_integrity", "LINK_UNRESOLVED")
                continue
            text = read(target).decode("utf-8")
            headings = {line[3:] for line in text.splitlines() if line.startswith("## ")}
            if separator and fragment not in headings:
                fail("evidence_integrity", "LINK_UNRESOLVED")
        except BoundaryError:
            fail("preflight", "UNSAFE_PATH")
    return result(complete=True)
