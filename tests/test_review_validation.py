"""Synthetic falsifications of Review validator outputs."""
from copy import deepcopy
import os
import unittest
from uuid import uuid4

from runner import review_validation as oracle
from runner.paths import RESULTS, no_links


OBSERVED = dict(launched=True, exit_code=0, stop_reason=None, job_drained=True,
                evidence_redacted=False)


class ReviewValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definitions, cls.gold = oracle.suite_inputs()
        cls.cases = oracle.index(cls.definitions["cases"])
        if not RESULTS.is_dir():
            raise unittest.SkipTest("BLOCKED: authorized external results root unavailable")
        no_links(RESULTS)
        cls.evidence_root = RESULTS / ("review-validation-" + uuid4().hex)
        cls.evidence_root.mkdir()

    def setup_case(self, cid, purpose=""):
        definition = self.cases[cid]
        directory = self.evidence_root / (cid + purpose)
        manifest, candidate, _, _ = oracle.prepare(directory, definition, self.definitions, self.gold)
        before = oracle.inventory(directory, allow_reparse=True)
        return definition, directory, manifest, candidate, before

    def fixture_view(self, case):
        definition, directory, manifest, candidate, _ = case
        expectation = self.gold["case_expectations"][definition["id"]]
        view = deepcopy(candidate) if expectation.get("projection") else {"schema_version": 1}
        if expectation.get("projection"):
            sources = oracle.index(manifest["sources"])
            for citation in view["citations"]:
                raw = (directory / "sources" / sources[citation["source_id"]]["relative_path"]).read_bytes()
                citation["start_line"] = raw.count(b"\n", 0, citation["start_byte"]) + 1
                citation["end_line"] = raw.count(b"\n", 0, citation["end_byte"] - 1) + 1
            for dimension in oracle.DIMENSIONS:
                for row in view["results"][dimension]:
                    reason = self.gold["computed_reasons"].get(row["id"], {}).get(row["status"])
                    if reason is not None:
                        row["reason"] = reason
        view["state"] = expectation["state"]
        reference = expectation.get("diagnostic_reference", "R1")
        blocked = {"CANDIDATE_MISSING", "MANIFEST_MISSING", "SOURCE_MISSING",
                   "SOURCE_UNAVAILABLE", "CLAIM_UNVERIFIED"}
        checks = {
            code: dict(scope=reference, status="blocked" if code in blocked else "fail",
                       code=code, references=[reference])
            for code in expectation["codes"]
        }
        if expectation.get("projection"):
            def record(code, scope, refs, status="pass"):
                if refs:
                    checks[code] = dict(scope=scope, status=status, code=code, references=sorted(refs))
            record("INPUT_SHAPE_VALID", "structure", {"candidate", "manifest"})
            record("MANIFEST_BINDING_VALID", "binding", {"candidate", "manifest"})
            record("MANIFEST_REFERENCES_BOUND", "binding", {"manifest"})
            record("SOURCE_VERIFIED", "evidence", oracle.index(view["sources"]))
            record("CITATION_VERIFIED", "citations", oracle.index(view["citations"]))
            record("COVERAGE_LINKED", "coverage", {row["criterion_id"] for row in view["results"]["coverage"]})
            outcomes = {}
            for dimension in oracle.DIMENSIONS:
                for row in view["results"][dimension]:
                    outcomes.setdefault(row["status"], []).append(row["id"])
            for label in ("supported", "contradicted", "unverified", "not_applicable"):
                record("CLAIM_" + label.upper(), "semantics", outcomes.get(label, []),
                       "blocked" if label == "unverified" else "pass")
        view["validation"] = {
            "validator_version": self.gold["validator_version"],
            "checks": [dict(check, id=f"check-{n}") for n, check in enumerate(checks.values(), 1)],
        }
        return view

    def evaluate(self, view, case, *, observation=None, raw=None):
        definition, directory, manifest, candidate, before = case
        if raw is None:
            raw = oracle.json_bytes(view)
        outcome = oracle.evaluate(raw, directory, definition, self.gold, manifest, candidate, before,
                                  oracle.inventory(directory, allow_reparse=True), observation or OBSERVED)
        finding = {"view_sha256": oracle.digest(raw), "dimensions": outcome}
        with (self.evidence_root / "oracle-observations.jsonl").open("ab") as stream:
            stream.write(oracle.json_bytes(finding))
        return outcome

    def test_all_cases_materialize_disposable_inputs(self):
        for cid in self.cases:
            with self.subTest(case=cid):
                if cid == "G-junction" and os.name != "nt":
                    continue
                try:
                    definition, directory, manifest, candidate, before = self.setup_case(cid, "-material")
                except oracle.PrerequisiteError as error:
                    self.assertEqual(cid, "G-junction")
                    self.skipTest(f"BLOCKED junction prerequisite: {error}")
                self.assertEqual(oracle.digest(b"P7 synthetic outside-root sentinel\n"),
                                 before["outside.txt"]["sha256"])
                if (directory / "manifest.json").is_file():
                    self.assertIn("manifest.json", before)
                    if cid != "D-manifest":
                        self.assertEqual(before["manifest.json"]["sha256"],
                                         candidate["provenance"]["manifest_sha256"])
                self.assertEqual(manifest["schema_version"], 1)
                if cid == "G-junction":
                    link = directory / "sources/linked"
                    self.assertEqual(link.resolve(), directory / "sentinels")
                if cid == "G-absolute":
                    self.assertEqual(manifest["sources"][3]["relative_path"],
                                     (directory / "outside.txt").as_posix())
                if cid == "C-tests-unavailable" and os.name == "nt":
                    path = directory / "sources/tests.txt"
                    with oracle.exclusive_file(path):
                        with self.assertRaises(PermissionError):
                            oracle.bounded_read(path)
                    self.assertEqual(before["sources/tests.txt"]["sha256"],
                                     oracle.digest(oracle.bounded_read(path)))
                if cid.startswith("F-") and definition["operations"][0][0] == "json":
                    side = definition["operations"][0][1]
                    code = self.gold["case_expectations"][cid]["codes"][0]
                    if code in {"JSON_SYNTAX", "JSON_ENCODING", "JSON_DUPLICATE_KEY",
                                "SCHEMA_VERSION_UNSUPPORTED", "LIMIT_EXCEEDED"}:
                        raw = (directory / f"{side}.json").read_bytes()
                        with self.assertRaises(ValueError):
                            oracle.load_view(raw + b"\n")

    def test_expected_projections_and_rejection_are_distinct(self):
        for cid in ("A", "A-self-report", "B", "C-no-manifest", "C-no-candidate",
                    "C-tests-missing", "C-item-missing", "C-tests-unavailable",
                    "C-manual-required", "C-manual-optional", "C-manual-excluded",
                    "E-utf8", "E-utf8-boundary", "F-label", "F-manifest-escaped-duplicate"):
            with self.subTest(case=cid):
                case = self.setup_case(cid, "-exemplar")
                view = self.fixture_view(case)
                outcome = self.evaluate(view, case)
                self.assertEqual("PASS", oracle.aggregate(outcome), outcome)
                if cid == "E-utf8":
                    self.assertEqual("é\r\n", view["citations"][9]["quote"])

    def test_independent_falsifications(self):
        probes = (
            ("digest", "evidence_integrity",
             lambda view: view["sources"][3].update(sha256="0" * 64)),
            ("quote", "evidence_integrity",
             lambda view: view["citations"][1].update(quote="empty=null")),
            ("locator", "evidence_integrity",
             lambda view: view["citations"][1].update(start_byte=0, end_byte=14, quote="encoding=UTF-8")),
            ("support", "semantic_accuracy",
             lambda view: view["results"]["requirements"][0].update(citation_ids=["c1", "c8", "c3"])),
            ("coverage", "requirement_coverage", lambda view: view["results"]["coverage"].pop()),
            ("provenance", "provenance",
             lambda view: view["provenance"].update(manifest_sha256="0" * 64)),
            ("attestation", "semantic_accuracy",
             lambda view: view["validation"]["checks"][0].update(code="FAKE_CHECK")),
        )
        for purpose, dimension, mutate in probes:
            with self.subTest(purpose=purpose):
                case = self.setup_case("A", "-" + purpose)
                view = self.fixture_view(case)
                self.assertEqual("PASS", oracle.aggregate(self.evaluate(view, case)))
                mutate(view)
                outcome = self.evaluate(view, case)
                self.assertEqual("FAIL", outcome[dimension]["status"], outcome)
                self.assertEqual("FAIL", oracle.aggregate(outcome))

        case = self.setup_case("A", "-same-length-source-drift")
        view = self.fixture_view(case)
        self.assertEqual("PASS", oracle.aggregate(self.evaluate(view, case)))
        path = case[1] / "sources/head/export.txt"
        path.write_bytes(path.read_bytes().replace(b"empty=[]", b"empty={}"))
        outcome = self.evaluate(view, case)
        self.assertEqual("FAIL", outcome["evidence_integrity"]["status"], outcome)

        # A coherent replacement of bytes and metadata still must satisfy authored gold.
        case = self.setup_case("A", "-coherent-fact-drift")
        _, directory, manifest, candidate, _ = case
        view = self.fixture_view(case)
        head = directory / "sources/head/export.txt"
        data = head.read_bytes().replace(b"empty=[]", b"empty={}")
        head.write_bytes(data)
        for doc in (manifest, candidate, view):
            oracle.by_id(doc["sources"], "head")["sha256"] = oracle.digest(data)
        manifest_raw = oracle.json_bytes(manifest)
        (directory / "manifest.json").write_bytes(manifest_raw)
        for doc in (candidate, view):
            doc["provenance"]["manifest_sha256"] = oracle.digest(manifest_raw)
            oracle.by_id(doc["citations"], "c2")["quote"] = "empty={}"
        # Rebase preservation inventory deliberately to isolate the semantic gold.
        case = (*case[:-1], oracle.inventory(directory, allow_reparse=True))
        outcome = self.evaluate(view, case)
        self.assertEqual("PASS", outcome["evidence_integrity"]["status"], outcome)
        self.assertEqual("FAIL", outcome["semantic_accuracy"]["status"], outcome)

        case = self.setup_case("B", "-false-result")
        view = self.fixture_view(case)
        self.assertEqual("PASS", oracle.aggregate(self.evaluate(view, case)))
        view["results"]["requirements"][0]["status"] = "supported"
        self.assertEqual("FAIL", self.evaluate(view, case)["semantic_accuracy"]["status"])

    def test_rejected_envelope_cannot_pass_by_label_or_claim_projection(self):
        case = self.setup_case("E-unrelated", "-diagnostic")
        view = self.fixture_view(case)
        self.assertEqual("PASS", oracle.aggregate(self.evaluate(view, case)))
        view["validation"]["checks"][0]["status"] = "pass"
        self.assertEqual("FAIL", self.evaluate(view, case)["semantic_accuracy"]["status"])
        view["validation"]["checks"][0]["status"] = "fail"
        view["results"] = case[3]["results"]
        self.assertEqual("FAIL", self.evaluate(view, case)["semantic_accuracy"]["status"])

    def test_diagnostic_digests_use_original_input_bytes(self):
        case = self.setup_case("E-unrelated", "-diagnostic-digests")
        view = self.fixture_view(case)
        view["review_id"] = case[2]["review_id"]
        view["input_digests"] = {
            f"{name}_sha256": oracle.digest((case[1] / f"{name}.json").read_bytes())
            for name in ("candidate", "manifest")
        }
        self.assertEqual("PASS", oracle.aggregate(self.evaluate(view, case)))
        view["input_digests"]["candidate_sha256"] = "0" * 64
        self.assertEqual("FAIL", self.evaluate(view, case)["provenance"]["status"])

    def test_oracle_rejects_extra_attestations_and_unsupported_projection(self):
        case = self.setup_case("A", "-projection")
        original = self.fixture_view(case)
        for field, value in (
            ("tests_executed", True),
            ("validation.validator_version", "unbound-version"),
            ("validation.tests_executed", True),
            ("validation.checks.0.references", ["unknown"]),
            ("results.requirements.0.reason", "All live tests passed."),
            ("results.technical.0.severity", "critical"),
        ):
            with self.subTest(field=field):
                view = deepcopy(original)
                parent, key = oracle.parent_at(view, field)
                parent[key] = value
                self.assertEqual("FAIL", oracle.aggregate(self.evaluate(view, case)))
        view = deepcopy(original)
        del view["citations"][0]["start_line"]
        self.assertEqual("FAIL", self.evaluate(view, case)["evidence_integrity"]["status"])
        case = self.setup_case("D-bytes", "-conflicting-source")
        view = self.fixture_view(case)
        failure = view["validation"]["checks"][0]
        failure["references"] = ["head"]
        self.assertEqual("PASS", oracle.aggregate(self.evaluate(view, case)))
        view["validation"]["checks"].append(
            dict(id="conflict", scope="evidence", status="pass", code="SOURCE_VERIFIED", references=["head"]))
        self.assertEqual("FAIL", oracle.aggregate(self.evaluate(view, case)))

    def test_validation_cannot_omit_checks_or_invent_external_evidence(self):
        case = self.setup_case("A", "-diagnostic-coverage")
        original = self.fixture_view(case)
        view = deepcopy(original)
        view["validation"]["checks"] = [row for row in view["validation"]["checks"]
                                         if row["code"] != "CLAIM_SUPPORTED"]
        self.assertEqual("FAIL", oracle.aggregate(self.evaluate(view, case)))
        view = deepcopy(original)
        next(row for row in view["validation"]["checks"]
             if row["code"] == "CLAIM_SUPPORTED")["references"].remove("R1")
        self.assertEqual("FAIL", oracle.aggregate(self.evaluate(view, case)))
        view = deepcopy(original)
        view["results"]["coverage"][0]["reason"] = "All tests passed on the real PR."
        self.assertEqual("FAIL", self.evaluate(view, case)["requirement_coverage"]["status"])

        case = self.setup_case("D-bytes", "-diagnostic-conflicts")
        original = self.fixture_view(case)
        view = deepcopy(original)
        view["validation"]["checks"][0]["references"].clear()
        self.assertEqual("FAIL", oracle.aggregate(self.evaluate(view, case)))
        view = deepcopy(original)
        view["validation"]["checks"] += [
            dict(id="supported", scope="semantics", status="pass", code="CLAIM_SUPPORTED", references=["R1"]),
            dict(id="unsupported", scope="semantics", status="fail", code="CLAIM_UNSUPPORTED", references=["R1"]),
        ]
        self.assertEqual("FAIL", oracle.aggregate(self.evaluate(view, case)))
        view = deepcopy(original)
        view["validation"]["checks"].append(dict(
            id="invented", scope="evidence", status="fail", code="LIVE_TESTS_FAILED", references=["external-test-run"]))
        self.assertEqual("FAIL", oracle.aggregate(self.evaluate(view, case)))

    def test_technical_failures_and_not_run_never_become_pass(self):
        case = self.setup_case("A", "-technical")
        view = self.fixture_view(case)
        for raw in (b"", b"{}\n", oracle.json_bytes(view)[:-1], b" " * oracle.MIB + b"\n",
                    oracle.json_bytes(view) * 2, b'\xef\xbb\xbf' + oracle.json_bytes(view)):
            with self.subTest(size=len(raw)):
                outcome = self.evaluate(view, case, raw=raw)
                self.assertEqual("FAIL", outcome["completion"]["status"])
                self.assertEqual("NOT_RUN", outcome["semantic_accuracy"]["status"])
        timeout = self.evaluate(view, case, observation=dict(OBSERVED, stop_reason="wall-timeout"))
        self.assertEqual("FAIL", timeout["completion"]["status"])
        absent = self.evaluate(view, case, observation=dict(OBSERVED, launched=False))
        self.assertEqual("NOT_RUN", absent["completion"]["status"])
        self.assertEqual("BLOCKED", oracle.aggregate(absent))
        self.assertEqual("NOT_RUN", oracle.not_started("NOT_RUN", "No pin selected.")["overall"])


if __name__ == "__main__":
    unittest.main()
