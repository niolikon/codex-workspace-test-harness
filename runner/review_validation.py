"""Review input materialization and independent validator output checks."""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures/review-validation"
MIB = 1024 * 1024
DIMENSIONS = ("requirements", "guidelines", "technical")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
DIAGNOSTIC_FIELDS = {"schema_version", "state", "validation", "review_id", "input_digests"}
VIEW_FIELDS = (DIAGNOSTIC_FIELDS - {"input_digests"} |
               {"scope", "sources", "provenance", "citations", "results", "limitations"})


class PrerequisiteError(Exception):
    """A local fixture prerequisite is unavailable."""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def bounded_read(path, size=MIB):
    with path.open("rb") as stream:
        data = stream.read(size + 1)
    require(len(data) <= size, f"file exceeds byte limit: {path.name}")
    return data


def unique_object(pairs):
    require(len(pairs) <= 256, "object key count")
    result = dict(pairs)
    require(len(pairs) == len(result), "duplicate JSON key")
    return result


def integer(token):
    require(len(token) <= 11, "JSON integer range")
    value = int(token)
    require(-(2**31) <= value < 2**31, "JSON integer range")
    return value


def forbidden_number(_):
    raise ValueError("noninteger JSON token")


def load_view(raw):
    """Parse a bounded byte stream independently of the Product parser."""
    require(0 < len(raw) <= MIB and raw.endswith(b"\n"), "view byte limit or missing LF")
    require(not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw, "view BOM or raw CR")
    text = raw.decode("utf-8")
    # Bound parser recursion before calling the stdlib decoder. Strings are skipped
    # as lexemes, so quoted delimiters do not contribute to depth.
    depth = 0
    for match in re.finditer(r'"(?:[^"\\]|\\.)*"|[][{}]', text, re.DOTALL):
        token = match[0]
        if token in ("[", "{"):
            depth += 1
            require(depth <= 32, "view nesting depth")
        elif token in ("]", "}"):
            depth -= 1
    value = json.loads(text, object_pairs_hook=unique_object, parse_int=integer,
                       parse_float=forbidden_number, parse_constant=forbidden_number)
    require(type(value) is dict and type(value.get("schema_version")) is int
            and value["schema_version"] == 1, "view must be a v1 object")
    pending = [value]
    count = 0
    while pending:
        node = pending.pop()
        count += 1
        require(count <= 32768, "view value count")
        if isinstance(node, dict):
            require(len(node) <= 256, "view object member count")
            require(all(len(key.encode("utf-8")) <= 16384 for key in node), "view key size")
            pending.extend(node.values())
        elif isinstance(node, list):
            require(len(node) <= 256, "view array count")
            pending.extend(node)
        elif isinstance(node, str):
            require(len(node.encode("utf-8")) <= 16384, "view string size")
    return value


def fixture(name):
    # These checked-in definitions can exceed per-ingress structural cardinality.
    return json.loads(bounded_read(FIXTURES / name), object_pairs_hook=unique_object)


def index(rows):
    require(isinstance(rows, list), "expected collection")
    result = {}
    for row in rows:
        key = row.get("id") if type(row) is dict else None
        require(isinstance(key, str) and ID.fullmatch(key) and key not in result,
                "invalid or duplicate ID in view")
        result[key] = row
    return result


def relative_entry(root, name):
    """Resolve a regular preserved file, with ordinal components and no reparse."""
    relative = Path(name)
    require(not relative.is_absolute() and relative.as_posix() == name and
            ".." not in relative.parts, f"unsafe inventory path: {name}")
    current = root
    for part in relative.parts:
        names = {entry.name: entry for entry in current.iterdir()}
        require(part in names, f"missing or noncanonical component: {name}")
        current = names[part]
        require(not is_reparse(current.lstat()), f"reparse inventory component: {name}")
    require(stat.S_ISREG(current.lstat().st_mode), f"nonregular file: {name}")
    return current


def is_reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def inventory(root, *, allow_reparse=False):
    """Record the whole tree without walking any link or junction."""
    require(not is_reparse(root.lstat()), "inventory root is a reparse point")
    result = {}
    pending = [root]
    while pending:
        parent = pending.pop()
        for path in parent.iterdir():
            require(len(result) < 512, "inventory entry limit")
            name = path.relative_to(root).as_posix()
            info = path.lstat()
            if is_reparse(info):
                require(allow_reparse, f"unexpected reparse point: {name}")
                result[name] = {"reparse": os.readlink(path)}
            elif stat.S_ISDIR(info.st_mode):
                result[name] = {"directory": True}
                pending.append(path)
            else:
                require(stat.S_ISREG(info.st_mode), f"nonregular inventory member: {name}")
                # Sources may intentionally exceed 1 MiB by one byte.
                raw = bounded_read(path, MIB + 1)
                result[name] = {"bytes": len(raw), "sha256": digest(raw)}
    return dict(sorted(result.items()))


def line_locator(raw, line_number):
    """Source intervals exclude the final LF or CRLF of each selected line."""
    lines = raw.split(b"\n")
    line = lines[line_number - 1].removesuffix(b"\r")
    require(bool(line), "empty prescribed citation")
    start = sum(len(previous) + 1 for previous in lines[:line_number - 1])
    return start, start + len(line)


# Input locator recipe uses line identities. Gold independently fixes byte spans.
LINE_RECIPE = (("item", 2), ("head", 2), ("tests", 1), ("item", 3),
               ("head", 3), ("tests", 2), ("guide", 1), ("head", 1), ("base", 3))
SOURCE_KINDS = dict(item="work_item", guide="guideline", base="base_file",
                    head="head_file", tests="test_evidence")


def citation(cid, source_id, raw, start, end):
    return dict(id=cid, source_id=source_id, start_byte=start, end_byte=end,
                quote=raw[start:end].decode("utf-8"))


def material(variant, definitions, gold):
    data = {key: entry["text"].encode("utf-8")
            for key, entry in definitions["sources"].items()}
    for key, raw in data.items():
        require(digest(raw) == definitions["sources"][key]["sha256"], f"handoff digest {key}")
    target = dict(repository_id="fixture/export", pr_id=None, base="snapshot:b1",
                  head="snapshot:h1", algorithm="synthetic")
    if variant == "B":
        data["head"] = data["head"].replace(b"empty=[]", b"empty=null")
        data["tests"] = data["tests"].replace(b"empty: []", b"empty: null")
        target["head"] = "snapshot:h2"
    if variant == "utf8":
        data["guide"] = b"\xc3\xa9\r\n" + data["guide"].replace(b"\n", b"\r\n")
    sources = []
    for key, kind in SOURCE_KINDS.items():
        raw = data[key]
        source = dict(id=key, kind=kind, revision=digest(raw),
                      relative_path=definitions["sources"][key]["path"], bytes=len(raw),
                      sha256=digest(raw), origin="synthetic_fixture",
                      captured_at_utc="2026-11-21T08:00:00Z")
        if key in ("base", "head", "tests"):
            source.update(repository_id=target["repository_id"],
                          revision=target["base" if key == "base" else "head"])
        if key in ("base", "head"):
            source["side"] = key
        sources.append(source)
    scope = dict(work_item=dict(id="WI-701", source_id="item", sha256=digest(data["item"])),
                 target=target)
    provenance = dict(producer_id="fixture-harness", producer_version="1",
                      normative_version="RV-01", candidate_id=f"rv01-{variant.lower()}")
    manifest = dict(schema_version=1, review_id=f"rv01-{variant.lower()}", scope=scope,
                    provenance=provenance, sources=sources, criteria=[])
    candidate = deepcopy(manifest)
    del candidate["criteria"]
    candidate["provenance"]["mode"] = "offline"
    candidate["limitations"] = []
    candidate["citations"] = [
        citation(f"c{count}", source, data[source],
                 *line_locator(data[source], 2 if variant == "utf8" and source == "guide" else line))
        for count, (source, line) in enumerate(LINE_RECIPE, 1)]
    if variant == "utf8":
        candidate["citations"].append(citation("c10", "guide", data["guide"], 0, 4))
    offsets = [[row["start_byte"], row["end_byte"]] for row in candidate["citations"]]
    require(offsets == gold["offsets"][variant], f"derived handoff offsets: {variant}")

    results = {name: [] for name in (*DIMENSIONS, "coverage")}
    for cid, criterion in gold["criteria"].items():
        manifest["criteria"].append(dict(id=cid, applicability="required", **{
            key: deepcopy(criterion[key]) for key in ("dimension", "statement", "rule", "roles")
        }))
        result = dict(id=cid, statement=criterion["statement"],
                      status="contradicted" if variant == "B" and cid == "R1" else "supported",
                      reason="Values in the supplied synthetic evidence.",
                      citation_ids=criterion["citations"].copy())
        if criterion["dimension"] == "technical":
            result["severity"] = "info"
        results[criterion["dimension"]].append(result)
        results["coverage"].append(dict(criterion_id=cid, result_id=cid, reason="Evaluated."))
    candidate["results"] = results
    return manifest, candidate, data


def by_id(rows, key):
    return next(row for row in rows if row["id"] == key)


def parent_at(value, path):
    *parts, key = path.split(".")
    for part in parts:
        value = value[int(part) if isinstance(value, list) else part]
    return value, int(key) if isinstance(value, list) else key


def add_gap(manifest, candidate, source, code):
    affected = [row["id"] for row in manifest["criteria"] if source in row["roles"].values()]
    removed = {row["id"] for row in candidate["citations"] if row["source_id"] == source}
    candidate["sources"] = [row for row in candidate["sources"] if row["id"] != source]
    candidate["citations"] = [row for row in candidate["citations"] if row["id"] not in removed]
    reason = "The expected source could not be acquired."
    for cid in affected:
        result = by_id(candidate["results"]["requirements"], cid)
        result.update(status="unverified", reason=reason,
                      citation_ids=[ref for ref in result["citation_ids"] if ref not in removed])
    candidate["limitations"].append(
        dict(code=code, reference=source, criterion_ids=affected, reason=reason))


def add_manual(manifest, candidate, applicability):
    statement = "The export satisfies every external consumer."
    manifest["criteria"].append(dict(id="M1", dimension="requirements",
                                     applicability=applicability, statement=statement,
                                     rule="manual", roles={}))
    excluded = applicability == "excluded"
    reason = "Excluded by manifest." if excluded else "No external consumer evidence was supplied."
    candidate["results"]["requirements"].append(dict(
        id="M1", statement=statement, status="not_applicable" if excluded else "unverified",
        reason=reason, citation_ids=[]))
    candidate["results"]["coverage"].append(dict(criterion_id="M1", result_id="M1", reason=reason))


def raw_mutation(raw, flavor):
    body = raw.rstrip(b"\n")
    if flavor == "size":
        return b" " * (MIB + 1 - len(body)) + body
    if flavor == "version":
        return body.replace(b'"schema_version":1', b'"schema_version":2', 1)
    if flavor == "key-case":
        return body.replace(b'"schema_version"', b'"Schema_version"', 1)
    if flavor == "type":
        return body.replace(b'"schema_version":1', b'"schema_version":"1"', 1)
    special = {
        "truncated": body[:-1], "comment": b"/* test */" + body, "trailing-comma": body[:-1] + b",}",
        "trailing-token": body + b"{}", "root-array": b"[" + body + b"]",
        "bom": b"\xef\xbb\xbf" + body, "invalid-utf8": body[:-1] + b',"probe":"\xff"}',
    }
    if flavor in special:
        return special[flavor]
    # One isolating input per limit; lexical validation must precede schema checking.
    fields = {
        "duplicate": b'"schema_version":1',
        "escaped-duplicate": br'"schema_\u0076ersion":1',
        "unknown": b'"probe":0', "surrogate": br'"probe":"\ud800"',
        "depth": b'"probe":' + b"[" * 32 + b"0" + b"]" * 32,
        "array-count": b'"probe":[' + b",".join([b"0"] * 257) + b"]",
        "key-count": b",".join(f'"probe{i}":0'.encode() for i in range(257)),
        "value-count": b'"probe":[' + b",".join([b"[" + b",".join([b"0"] * 256) + b"]"] * 128) + b"]",
        "string-size": b'"probe":"' + b"\xc3\xa9" * 8193 + b'"',
        "key-size": b'"' + b"\xc3\xa9" * 8193 + b'":0',
        "integer-range": b'"probe":2147483648',
        "noninteger": b'"probe":1.0',
    }
    return body[:-1] + b"," + fields[flavor] + b"}"


def increase_sources(manifest, candidate, data, kind):
    """Bind deliberately oversized or invalid UTF-8 snapshots to their real bytes."""
    if kind in ("encoding", "individual"):
        raw = b"\xff" if kind == "encoding" else b"x" * (MIB + 1)
        data["head"] = raw
        by_id(manifest["sources"], "head").update(bytes=len(raw), sha256=digest(raw))
        by_id(candidate["sources"], "head").update(bytes=len(raw), sha256=digest(raw))
        return
    for number in range(9):
        cid = f"extra-{number}"
        raw = b"x" * MIB
        data[cid] = raw
        source = deepcopy(by_id(manifest["sources"], "guide"))
        source.update(id=cid, relative_path=f"extra/{cid}.txt", bytes=len(raw),
                      revision=digest(raw), sha256=digest(raw))
        manifest["sources"].append(source)
        candidate["sources"].append(deepcopy(source))


def write_json(path, value):
    path.write_bytes(json_bytes(value))


def create_junction(directory):
    if os.name != "nt":
        raise PrerequisiteError("A Windows filesystem is required to create the junction.")
    cmd = Path(os.environ["SystemRoot"]) / "System32/cmd.exe"
    try:
        process = subprocess.run([str(cmd), "/D", "/C", "mklink", "/J",
                                  r"sources\linked", "sentinels"], cwd=directory,
                                 capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PrerequisiteError(f"Junction creation unavailable: {type(error).__name__}") from error
    if process.returncode or not is_reparse((directory / "sources/linked").lstat()):
        raise PrerequisiteError(f"Junction creation failed (exit {process.returncode}).")


def prepare(directory, definition, definitions, gold):
    """Create synthetic artifacts in a new case directory within a disposable run."""
    directory.mkdir()
    source_root = directory / "sources"
    source_root.mkdir()
    (directory / "sentinels").mkdir()
    sentinel = b"P7 synthetic outside-root sentinel\n"
    (directory / "outside.txt").write_bytes(sentinel)
    (directory / "sentinels/outside.txt").write_bytes(sentinel)
    (source_root / "sentinel.txt").write_bytes(b"P7 synthetic inside-root sentinel\n")
    manifest, candidate, data = material(definition["variant"], definitions, gold)
    paths = {row["id"]: row["relative_path"] for row in manifest["sources"]}
    omitted, bad_json = set(), {}
    lock, junction = None, False
    for op, *args in definition["operations"]:
        if op in ("set", "delete"):
            side, path, *value = args
            parent, key = parent_at(manifest if side == "manifest" else candidate, path)
            if op == "set":
                parent[key] = value[0]
            else:
                del parent[key]
        elif op == "duplicate":
            path, position, cid = args
            parent, key = parent_at(candidate, path)
            rows = parent[key]
            rows.append(dict(deepcopy(rows[position]), id=cid))
        elif op == "omit":
            omitted.add(args[0])
        elif op == "gap":
            cid, mode, code = args
            add_gap(manifest, candidate, cid, code)
            if mode == "unavailable":
                lock = source_root / paths[cid]
            else:
                del data[cid]
        elif op == "manual":
            add_manual(manifest, candidate, args[0])
        elif op == "replace-bytes":
            cid, old, new = args
            require(data[cid].count(old.encode()) == 1, "drift mutation did not isolate a value")
            data[cid] = data[cid].replace(old.encode(), new.encode())
        elif op == "redeclare":
            cid = args[0]
            by_id(candidate["sources"], cid)["sha256"] = digest(data[cid])
        elif op == "json":
            bad_json[args[0]] = args[1]
        elif op == "source-limit":
            increase_sources(manifest, candidate, data, *args)
        elif op in ("manifest-path", "candidate-path"):
            path = args[0]
            if path == "{outside}":
                path = (directory / "outside.txt").as_posix()
            junction = path.startswith("linked/")
            document = manifest if op == "manifest-path" else candidate
            by_id(document["sources"], "head")["relative_path"] = path
        else:
            raise ValueError(f"unknown fixture operation {op}")
    manifest_raw = json_bytes(manifest)
    if "manifest" in bad_json:
        manifest_raw = raw_mutation(manifest_raw, bad_json["manifest"])
    candidate["provenance"].setdefault("manifest_sha256", digest(manifest_raw))
    candidate_raw = json_bytes(candidate)
    if "candidate" in bad_json:
        candidate_raw = raw_mutation(candidate_raw, bad_json["candidate"])
    for side, raw in (("candidate", candidate_raw), ("manifest", manifest_raw)):
        if side not in omitted:
            (directory / f"{side}.json").write_bytes(raw)
    for cid, raw in data.items():
        relative = paths[cid] if cid in paths else by_id(manifest["sources"], cid)["relative_path"]
        dest = source_root / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
    if junction:
        create_junction(directory)
    return manifest, candidate, paths, lock


@contextmanager
def exclusive_file(path):
    """Use a real Windows sharing denial while leaving the fixture bytes intact."""
    if os.name != "nt":
        raise PrerequisiteError("A Windows filesystem is required for sharing denial.")
    import ctypes as ct
    from ctypes import wintypes as wt

    kernel = ct.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                       wt.DWORD, wt.DWORD, wt.HANDLE)
    create.restype = wt.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wt.HANDLE,)
    handle = create(str(path), 0x80000000, 0, None, 3, 0, None)
    if handle == ct.c_void_p(-1).value:
        raise PrerequisiteError(f"Unable to acquire fixture lock (Win32 {ct.get_last_error()}).")
    try:
        yield
    finally:
        close(handle)


def projection_sources(view, candidate, manifest, directory):
    expected = index(candidate["sources"])
    actual = index(view["sources"])
    require(actual == expected, "source inventory or source metadata differs")
    manifest_sources = index(manifest["sources"])
    source_root = directory / "sources"
    evidence = {}
    for cid, source in actual.items():
        require(source == manifest_sources[cid], f"source {cid} differs from manifest")
        path = relative_entry(source_root, source["relative_path"])
        raw = bounded_read(path)
        require(len(raw) == source["bytes"], f"source size {cid}")
        require(digest(raw) == source["sha256"], f"source digest {cid}")
        raw.decode("utf-8")
        evidence[cid] = raw
    return evidence


def projection_citations(view, candidate, evidence, gold, variant):
    actual = index(view["citations"])
    expected = index(candidate["citations"])
    require(actual.keys() == expected.keys(), "citation inventory differs")
    for cid, row in actual.items():
        reference = expected[cid]
        offset = gold["offsets"][variant][int(cid[1:]) - 1]
        require([row.get("start_byte"), row.get("end_byte")] == offset, f"citation offsets {cid}")
        require(row.get("source_id") == reference["source_id"], f"citation source {cid}")
        raw = evidence[row["source_id"]]
        start, end = offset
        require(0 <= start < end <= len(raw), f"citation range {cid}")
        raw[:start].decode("utf-8")
        quote = raw[start:end].decode("utf-8")
        require(row.get("quote") == quote, f"citation quote {cid}")
        lines = (1 + raw.count(b"\n", 0, start), 1 + raw.count(b"\n", 0, end - 1))
        for key, wanted in zip(("start_line", "end_line"), lines):
            require(type(row.get(key)) is int and row[key] == wanted, f"citation line {cid}")
        require(set(row) == set(reference) | {"start_line", "end_line"}, f"citation shape {cid}")
    return actual


def fact_values(raw, kind, fields):
    separator = ": " if kind == "tests" else "="
    lines = raw.decode("utf-8").split("\n")
    result = {}
    for key in fields:
        prefix = key + separator
        values = [line.removesuffix("\r")[len(prefix):] for line in lines if line.startswith(prefix)]
        require(len(values) == 1, f"invalid synthetic {kind} value {key}")
        result[key] = values[0]
    return result


def semantic_results(view, manifest, evidence, citations, gold, variant):
    facts = gold["facts"]["B" if variant == "B" else "A"]
    observed = {}
    for source, values in facts.items():
        if source in evidence:
            observed[source] = fact_values(evidence[source], source, values)
            require(observed[source] == values, f"gold fact mismatch in {source}")
    checks = {
        "R1": (("item", gold["criteria"]["R1"]["statement"]),
               ("head", "empty=" + facts["head"]["empty"]),
               ("tests", "empty: " + facts["tests"]["empty"])),
        "R2": (("item", gold["criteria"]["R2"]["statement"]),
               ("head", "nonempty=" + facts["head"]["nonempty"]),
               ("tests", "nonempty: " + facts["tests"]["nonempty"])),
        "G1": (("guide", gold["criteria"]["G1"]["statement"]),
               ("head", "encoding=" + facts["head"]["encoding"])),
        "T1": (("base", "nonempty=" + facts["base"]["nonempty"]),
               ("head", "nonempty=" + facts["head"]["nonempty"])),
    }
    criteria = index(manifest["criteria"])
    require(set(view["results"]) == {*DIMENSIONS, "coverage"}, "results dimensions")
    for dimension in DIMENSIONS:
        found = index(view["results"][dimension])
        expected = {cid for cid, row in criteria.items() if row["dimension"] == dimension}
        require(found.keys() == expected, f"result inventory for {dimension}")
        for cid, row in found.items():
            criterion = criteria[cid]
            required_fields = {"id", "statement", "status", "reason", "citation_ids"}
            allowed_fields = required_fields | ({"severity"} if dimension == "technical" else set())
            require(required_fields <= set(row) <= allowed_fields and
                    ("severity" not in row or row["severity"] in ("info","low","medium","high","critical")),
                    f"result shape {cid}")
            require(row["statement"] == criterion["statement"] and
                    isinstance(row["reason"], str) and bool(row["reason"].strip()), f"statement or reason {cid}")
            refs = row["citation_ids"]
            require(isinstance(refs, list) and all(isinstance(ref, str) for ref in refs) and
                    len(refs) == len(set(refs)) and all(ref in citations for ref in refs),
                    f"claim references {cid}")
            if cid not in checks:
                expected_status = "not_applicable" if criterion["applicability"] == "excluded" else "unverified"
                require(row["status"] == expected_status and not refs, f"manual result {cid}")
                continue
            norm = gold["criteria"][cid]
            require(all(criterion[field] == norm[field] for field in ("roles", "rule", "dimension", "statement")),
                    f"gold roles or criterion changed for {cid}")
            applicable_checks = {pair for pair in checks[cid] if pair[0] in evidence}
            actual_checks = {(citations[ref]["source_id"], citations[ref]["quote"]) for ref in refs}
            require(actual_checks == applicable_checks, f"claim support roles or content {cid}")
            expected_status = "supported"
            if len(applicable_checks) != len(checks[cid]):
                expected_status = "unverified"
            elif cid == "R1" and observed["head"]["empty"] != "[]":
                expected_status = "contradicted"
            require(row["status"] == expected_status, f"claim status {cid}")
            if expected_status in ("supported", "contradicted"):
                require(row["reason"] == gold["computed_reasons"][cid][expected_status],
                        f"computed claim reason {cid}")
                if dimension == "technical":
                    require(row.get("severity") == "info", f"unverified technical severity {cid}")


def check_coverage(view, manifest, candidate):
    rows = view["results"]["coverage"]
    expected = {row["id"] for row in manifest["criteria"]}
    reasons = {row["criterion_id"]: row["reason"] for row in candidate["results"]["coverage"]}
    covered = set()
    for row in rows:
        require(set(row) == {"criterion_id", "result_id", "reason"}, "coverage shape")
        cid = row["criterion_id"]
        require(cid in expected and cid not in covered and row["result_id"] == cid and
                isinstance(row["reason"], str) and bool(row["reason"].strip())
                and row["reason"] == reasons[cid], f"coverage {cid}")
        covered.add(cid)
    require(covered == expected, "coverage missing")
    limitations = view["limitations"]
    expected_limits = candidate["limitations"]
    sort_key = lambda row: json.dumps(row, sort_keys=True, ensure_ascii=True)
    require(sorted(limitations, key=sort_key) == sorted(expected_limits, key=sort_key),
            "limitations missing or changed")


def check_provenance(view, manifest, candidate, directory):
    require(view["scope"] == manifest["scope"] and view["review_id"] == manifest["review_id"],
            "scope or review ID differs")
    expected = dict(manifest["provenance"], mode="offline",
                    manifest_sha256=digest(bounded_read(directory / "manifest.json")))
    require(candidate["provenance"] == expected == view["provenance"], "provenance binding differs")


def check_diagnostic_provenance(view, directory):
    """Check diagnostic attestations against the original, bounded input bytes."""
    inputs = {"candidate_sha256": directory / "candidate.json",
              "manifest_sha256": directory / "manifest.json"}
    digests = view.get("input_digests", {})
    require(isinstance(digests, dict) and digests.keys() <= inputs.keys(),
            "unknown diagnostic digest field")
    for key, declared in digests.items():
        require(declared == digest(bounded_read(inputs[key])), f"diagnostic {key}")
    if "review_id" in view:
        manifest = json.loads(bounded_read(inputs["manifest_sha256"]), object_pairs_hook=unique_object)
        review_id = view["review_id"]
        require(isinstance(review_id, str) and ID.fullmatch(review_id)
                and review_id == manifest["review_id"], "diagnostic review ID differs from manifest")


def validation_checks(view, expectation, validator_version, manifest, candidate):
    validation = view.get("validation")
    require(isinstance(validation, dict) and set(validation) == {"validator_version", "checks"},
            "validator record shape")
    require(validation["validator_version"] == validator_version, "unverified validator identity")
    checks = list(index(validation["checks"]).values())
    require(bool(checks), "missing validation checks")
    required_codes = set(expectation["codes"])
    observed = set()
    sources = set(index(manifest["sources"]))
    criteria = set(index(manifest["criteria"]))
    citations = set(index(candidate["citations"]))
    positive = {
        "INPUT_SHAPE_VALID": ("structure", {"candidate", "manifest"}),
        "MANIFEST_BINDING_VALID": ("binding", {"candidate", "manifest"}),
        "MANIFEST_REFERENCES_BOUND": ("binding", {"manifest"}),
        "SOURCE_VERIFIED": ("evidence", sources),
        "CITATION_VERIFIED": ("citations", citations),
        "CLAIM_SUPPORTED": ("semantics", criteria),
        "CLAIM_CONTRADICTED": ("semantics", criteria),
        "CLAIM_NOT_APPLICABLE": ("semantics", criteria),
        "COVERAGE_LINKED": ("coverage", criteria),
        "COVERAGE_EXCLUDED": ("coverage", criteria),
    }
    failed_codes = {
        "CITATION_QUOTE_MISMATCH", "CITATION_RANGE", "CITATION_SOURCE_MISSING",
        "CITATION_UTF8_BOUNDARY", "CLAIM_BINDING_MISMATCH", "CLAIM_SEVERITY_UNSUPPORTED",
        "CLAIM_STATUS_MISMATCH", "CLAIM_UNSUPPORTED", "COVERAGE_MISSING",
        "COVERAGE_RESULT_MISMATCH", "DUPLICATE_CITATION", "DUPLICATE_FINDING", "DUPLICATE_ID",
        "INPUT_CHANGED", "JSON_DUPLICATE_KEY", "JSON_ENCODING", "JSON_SYNTAX", "LIMIT_EXCEEDED",
        "MANIFEST_BINDING_MISMATCH", "REFERENCE_UNKNOWN", "SCHEMA_INVALID",
        "SCHEMA_VERSION_UNSUPPORTED", "SOURCE_BINDING_MISMATCH", "SOURCE_CHANGED",
        "SOURCE_DIGEST_MISMATCH", "SOURCE_ENCODING", "SOURCE_PATH_ABSOLUTE", "SOURCE_PATH_ADS",
        "SOURCE_PATH_ALIAS", "SOURCE_PATH_RESERVED", "SOURCE_PATH_TRAVERSAL", "SOURCE_REPARSE_POINT",
        "SOURCE_SIZE_MISMATCH", "TARGET_BINDING_MISMATCH", "WORK_ITEM_BINDING_MISMATCH",
    }
    blocked_codes = {
        "CANDIDATE_MISSING", "CANDIDATE_UNAVAILABLE", "CITATION_SOURCE_UNBOUND",
        "CLAIM_UNVERIFIED", "COVERAGE_GAP", "MANIFEST_MISSING", "MANIFEST_UNAVAILABLE",
        "SOURCE_MISSING", "SOURCE_NOT_ACQUIRED", "SOURCE_UNAVAILABLE",
    }
    status_codes = {"pass": positive, "fail": failed_codes, "blocked": blocked_codes,
                    "not_run": {"EVIDENCE_NOT_EVALUATED", "CLAIMS_NOT_EVALUATED"}}
    by_code = {}
    reference = expectation.get("diagnostic_reference")
    seen_reference = reference is None
    for row in checks:
        require(set(row) == {"id", "scope", "status", "code", "references"}, "check shape")
        code, status, refs = row["code"], row["status"], row["references"]
        require(isinstance(code, str) and ID.fullmatch(code) and code != "FAKE_CHECK"
                and isinstance(row["scope"], str) and row["scope"].strip(), "check code or scope")
        require(status in status_codes and code in status_codes[status] and isinstance(refs, list) and
                all(isinstance(item, str) and item for item in refs) and len(refs) == len(set(refs)),
                "unknown check code, status or references")
        by_code.setdefault(code, set()).update(refs)
        if status == "pass":
            require(code in positive and row["scope"] == positive[code][0] and
                    refs and set(refs) <= positive[code][1], "unexpected positive check or reference")
        if code in required_codes:
            require(status == ("blocked" if code in blocked_codes else "fail"), f"unsubstantiated {code}")
            observed.add(code)
            if reference is not None and reference in refs:
                seen_reference = True
        if expectation["state"] == "complete" and status != "pass":
            require(code in required_codes, f"unexpected negative check in complete view: {code}")
    require(required_codes <= observed, f"missing codes {sorted(required_codes - observed)}")
    require(seen_reference, f"diagnostic does not reference {reference}")
    for verified, unresolved in (
        (("SOURCE_VERIFIED",),
         ("SOURCE_DIGEST_MISMATCH", "SOURCE_SIZE_MISMATCH", "SOURCE_ENCODING", "SOURCE_MISSING",
          "SOURCE_UNAVAILABLE", "SOURCE_CHANGED")),
        (("CLAIM_SUPPORTED", "CLAIM_CONTRADICTED", "CLAIM_NOT_APPLICABLE"),
         ("CLAIM_UNSUPPORTED", "CLAIM_STATUS_MISMATCH", "CLAIM_UNVERIFIED")),
    ):
        seen = set().union(*(by_code.get(code, set()) for code in unresolved))
        for code in verified:
            refs = by_code.get(code, set())
            require(seen.isdisjoint(refs), "contradictory check references")
            seen.update(refs)
    if expectation.get("projection"):
        approved = {
            "INPUT_SHAPE_VALID": {"candidate", "manifest"},
            "MANIFEST_BINDING_VALID": {"candidate", "manifest"},
            "MANIFEST_REFERENCES_BOUND": {"manifest"},
            "SOURCE_VERIFIED": set(index(view["sources"])),
            "CITATION_VERIFIED": set(index(view["citations"])),
            "COVERAGE_LINKED": {row["criterion_id"] for row in candidate["results"]["coverage"]},
        }
        claim_codes = {"supported": "CLAIM_SUPPORTED", "contradicted": "CLAIM_CONTRADICTED",
                       "not_applicable": "CLAIM_NOT_APPLICABLE", "unverified": "CLAIM_UNVERIFIED"}
        for code in claim_codes.values():
            approved[code] = set()
        for dimension in DIMENSIONS:
            for row in view["results"][dimension]:
                approved[claim_codes[row["status"]]].add(row["id"])
        for code, refs in approved.items():
            require(by_code.get(code, set()) == refs, f"missing or surplus references for {code}")
    return sorted({row["code"] for row in checks})


def decision(status, reason):
    return {"status": status, "reason": reason}


def aggregate(report):
    priorities = ("FAIL", "BLOCKED", "NOT_RUN", "PASS", "SKIP")
    values = {row["status"] for row in report.values()}
    return next(status for status in priorities if status in values)


def evaluate(raw, directory, definition, gold, manifest, candidate, before, after, observation):
    """Evaluate the Product response; preserve separate reasons even for rejects."""
    expected = gold["case_expectations"][definition["id"]]
    report = {name: decision("NOT_RUN", "A prerequisite check has not passed.") for name in (
        "preflight", "completion", "tool_use", "evidence_integrity", "semantic_accuracy",
        "requirement_coverage", "provenance")}
    report["preflight"] = decision("PASS", "Pinned Product and disposable case inputs were selected.")
    launched = observation["launched"]
    report["tool_use"] = decision(
        "PASS" if launched else "NOT_RUN",
        "The supervised launcher was invoked." if launched else "The launcher did not start.")
    report["evidence_integrity"] = decision(
        "PASS" if before == after else "FAIL",
        "Input inventory and sentinels preserved." if before == after else "Input inventory changed.")
    if not launched:
        report["preflight"] = decision("BLOCKED", observation["stop_reason"] or "Launch unavailable.")
        return report
    try:
        require(observation["stop_reason"] is None and observation["exit_code"] == 0 and
                observation["job_drained"] and not observation["evidence_redacted"],
                "technical failure, capture truncation or redaction")
        view = load_view(raw)
    except (ValueError, TypeError, RecursionError) as error:
        report["completion"] = decision("FAIL", str(error))
        return report
    report["completion"] = decision("PASS", "One bounded UTF-8 JSON v1 view with final LF.")
    try:
        require(view.get("state") == expected["state"],
                f"state {view.get('state')!r} differs from gold {expected['state']}")
        if expected.get("projection"):
            require(view.keys() == VIEW_FIELDS, "review view shape or unbound attestation")
        else:
            require(view.keys() <= DIAGNOSTIC_FIELDS, "unbound diagnostic projection")
        validation_checks(view, expected, gold["validator_version"], manifest, candidate)
    except (ValueError, TypeError, KeyError, OSError) as error:
        report["semantic_accuracy"] = decision("FAIL", str(error))
        return report
    if not expected.get("projection"):
        for dimension, reason in {
            "semantic_accuracy": "State and diagnostics match the independent negative gold.",
            "requirement_coverage": "Unbound inputs produced no claim projection.",
        }.items():
            report[dimension] = decision("PASS", reason)
        try:
            check_diagnostic_provenance(view, directory)
        except (ValueError, TypeError, KeyError, OSError, RecursionError) as error:
            report["provenance"] = decision("FAIL", str(error))
        else:
            report["provenance"] = decision("PASS", "Diagnostic attestations match their original inputs.")
        return report

    try:
        evidence = projection_sources(view, candidate, manifest, directory)
        citations = projection_citations(view, candidate, evidence, gold, definition["variant"])
    except (ValueError, TypeError, IndexError, KeyError, OSError) as error:
        report["evidence_integrity"] = decision("FAIL", str(error))
    else:
        try:
            semantic_results(view, manifest, evidence, citations, gold, definition["variant"])
        except (ValueError, TypeError, IndexError, KeyError) as error:
            report["semantic_accuracy"] = decision("FAIL", str(error))
        else:
            report["semantic_accuracy"] = decision("PASS", "Gold facts, statuses and role support agree.")
    for dimension, check, reason in (
        ("requirement_coverage", lambda: check_coverage(view, manifest, candidate), "Gold coverage and limitations agree."),
        ("provenance", lambda: check_provenance(view, manifest, candidate, directory), "Manifest bytes, scope and identity agree."),
    ):
        try:
            check()
        except (ValueError, TypeError, IndexError, KeyError, OSError) as error:
            report[dimension] = decision("FAIL", str(error))
        else:
            report[dimension] = decision("PASS", reason)
    return report


def check_review_assets(product):
    relative = "template/.agents/skills/review"
    skill = bounded_read(relative_entry(product, f"{relative}/SKILL.md")).decode("utf-8")
    frontmatter = skill.splitlines()
    require(len(frontmatter) >= 4 and frontmatter[:2] == ["---", "name: review"]
            and "---" in frontmatter[2:], "Review skill frontmatter is missing or invalid")
    for name in ("review.schema.json", "manifest.schema.json"):
        raw = bounded_read(relative_entry(product, f"{relative}/{name}"))
        schema = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        require(isinstance(schema, dict), f"expected JSON schema in {name}")
        if name == "review.schema.json":
            branches = schema.get("anyOf")
            require(isinstance(branches, list) and
                    branches == [{"$ref": f"#/$defs/{key}"} for key in ("candidate", "view", "diagnostic")] and
                    all(schema.get("$defs", {}).get(key, {}).get("type") == "object"
                        for key in ("candidate", "view", "diagnostic")),
                    "expected candidate, view and diagnostic object branches")
        else:
            require(schema.get("type") == "object", f"expected object schema in {name}")
        pending = [schema]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in ("$ref", "$dynamicRef", "$recursiveRef"):
                        require(isinstance(value, str) and (value.startswith("#") or
                                value.split("#", 1)[0] in ("review.schema.json", "manifest.schema.json")),
                                f"nonlocal schema reference in {name}")
                    pending.append(value)
            elif isinstance(node, list):
                pending.extend(node)


def suite_inputs():
    definitions, gold = fixture("cases.json"), fixture("gold.json")
    cases = index(definitions["cases"])
    require(definitions["schema_version"] == gold["schema_version"] == 1 and
            cases.keys() == gold["case_expectations"].keys(),
            "fixture/gold case coverage differs")
    return definitions, gold


def not_started(status, reason):
    report = {name: decision("NOT_RUN", reason) for name in (
        "preflight", "completion", "tool_use", "evidence_integrity", "semantic_accuracy",
        "requirement_coverage", "provenance")}
    report["preflight"] = decision(status, reason)
    return {"execution": "NOT_RUN", "dimensions": report, "overall": aggregate(report)}
