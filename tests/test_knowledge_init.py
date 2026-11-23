"""P5B1 static contracts and independent synthetic F02 oracles; no inference."""
import json
import os
from pathlib import Path
import re
import unittest
import uuid
from runner.paths import RESULTS, no_links
import hashlib
import shutil
from runner.paths import local, snapshot
from tests.knowledge_init_oracle import evaluate


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/knowledge-init/bootstrap"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def edit_candidate(candidate, edit):
    parts = edit["path"].split("/")
    parent = candidate
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    key = int(parts[-1]) if isinstance(parent, list) else parts[-1]
    if edit["op"] == "set":
        parent[key] = edit["value"]
    elif edit["op"] == "append":
        parent[key].append(edit["value"])
    elif edit["op"] == "remove":
        del parent[key]
    else:
        raise ValueError("Unknown fixture edit")


class SyntheticOracleTests(unittest.TestCase):
    def test_independent_f02_scenarios(self):
        fixture_before = snapshot(FIXTURE)
        no_links(RESULTS)
        root = RESULTS / ("knowledge-init-oracle-" + uuid.uuid4().hex)
        root.mkdir()
        expected = json.loads((FIXTURE / "expectations.json").read_text(encoding="utf-8"))
        scenarios = json.loads((FIXTURE / "scenarios.json").read_text(encoding="utf-8"))
        write_json(root / "campaign.json", {"format": "knowledge-init-evidence-v1",
                   "campaign": root.name, "fixture_tree": fixture_before,
                   "fixture_root": "fixtures/knowledge-init/bootstrap", "mode": "offline",
                   "runtime": "NOT_RUN"})
        write_json(root / "fixture-snapshot.json", fixture_before)
        shutil.copyfile(FIXTURE / "expectations.json", root / "expectations.json")
        shutil.copyfile(FIXTURE / "scenarios.json", root / "scenarios.json")
        records = []
        for scenario in scenarios:
            with self.subTest(scenario=scenario["id"]):
                case = root / scenario["id"]
                case.mkdir()
                record = {"scenario": scenario["id"], "expected": scenario["expected_status"],
                          "required_codes": scenario["required_codes"], "test_status": "NOT_RUN"}
                try:
                    tree = case / "workspace"
                    shutil.copytree(FIXTURE / "before", tree)
                    candidate = json.loads((FIXTURE / "positive.json").read_text(encoding="utf-8"))
                    target = local(tree, expected["write_paths"][0])
                    if scenario["repeat"]:
                        target.write_bytes((FIXTURE / "expected-final.md").read_bytes())
                    before = snapshot(tree)
                    write_json(case / "before.json", before)
                    target.write_bytes((FIXTURE / "expected-final.md").read_bytes())
                    for mutation in scenario["filesystem"]:
                        path = local(tree, mutation["path"])
                        if mutation["op"] == "write":
                            path.write_text(mutation["value"], encoding="utf-8", newline="")
                        elif mutation["op"] == "remove":
                            path.unlink()  # Synthetic case construction in disposable tree only.
                        elif mutation["op"] == "mkdir":
                            path.mkdir()
                        else:
                            raise ValueError("Unknown fixture filesystem mutation")
                    for edit in scenario["edits"]:
                        edit_candidate(candidate, edit)
                    write_json(case / "candidate.json", candidate)
                    decision = evaluate(candidate, tree, expected, before)
                    write_json(case / "decision.json", decision)
                    write_json(case / "after.json", snapshot(tree))
                    record["observed"] = decision
                    self.assertEqual(scenario["expected_status"], decision["offline_overall"])
                    self.assertTrue(set(scenario["required_codes"]) <=
                                    {i["code"] for i in decision["issues"]}, decision)
                    self.assertEqual("NOT_RUN", decision["runtime_overall"])
                    record["test_status"] = "PASS"
                except Exception as error:
                    # Preserve unexpected errors, but never count them as rejection proof.
                    record["test_status"] = "FAIL"
                    record["error"] = (type(error).__name__ + ": " + str(error))[:2048]
                    raise
                finally:
                    try:
                        record["artifacts"] = snapshot(case)
                    except Exception as error:
                        # A damaged or inaccessible tree must not suppress the outcome.
                        record["test_status"] = "FAIL"
                        record["artifact_snapshot_error"] = (type(error).__name__ + ": " + str(error))[:2048]
                    write_json(case / "test-result.json", record)
                    records.append(record)
                    write_json(root / "results.json", records)
                    if "artifact_snapshot_error" in record:
                        self.fail("Evidence snapshot failed: " + record["artifact_snapshot_error"])
        self.assertEqual(fixture_before, snapshot(FIXTURE), "Source fixtures changed")
        write_json(root / "fixture-preservation.json", {"status": "PASS", "snapshot": fixture_before})

    def test_link_boundary_when_available(self):
        no_links(RESULTS)
        root = RESULTS / ("knowledge-init-link-" + uuid.uuid4().hex)
        root.mkdir()
        tree = root / "workspace"
        shutil.copytree(FIXTURE / "before", tree)
        expected = json.loads((FIXTURE / "expectations.json").read_text(encoding="utf-8"))
        candidate = json.loads((FIXTURE / "positive.json").read_text(encoding="utf-8"))
        before = snapshot(tree)
        local(tree, expected["write_paths"][0]).write_bytes((FIXTURE / "expected-final.md").read_bytes())
        outside = root / "outside"
        outside.mkdir()
        try:
            (tree / "redirect").symlink_to(outside, target_is_directory=True)
        except OSError as error:
            if getattr(error, "winerror", None) != 1314:
                raise
            write_json(root / "result.json", {"status": "SKIP", "reason": "Symlink privilege unavailable"})
            self.skipTest("SKIP: symlink privilege unavailable")
        decision = evaluate(candidate, tree, expected, before)
        write_json(root / "result.json", decision)
        self.assertEqual("FAIL", decision["offline_overall"])
        self.assertIn("UNSAFE_TREE", {i["code"] for i in decision["issues"]})


PRODUCT = Path(os.environ["CODEX_PRODUCT_ROOT"]).resolve() if os.environ.get("CODEX_PRODUCT_ROOT") else None

@unittest.skipUnless(PRODUCT, "NOT_RUN: select Product")
class KnowledgeInitStatic(unittest.TestCase):
    def test_reviewed_asset_integrity_and_mutations(self):
        expected = json.loads((FIXTURE.parent / "preserved.json").read_text(encoding="utf-8"))["protected_sha256"]
        for rel, digest in expected.items():
            data = (PRODUCT / rel).read_bytes()
            self.assertEqual(digest, hashlib.sha256(data).hexdigest(), rel)

    def test_disposable_forbidden_mutations(self):
        root = RESULTS / ("knowledge-init-contract-" + uuid.uuid4().hex)
        no_links(RESULTS)
        root.mkdir()
        expected = json.loads((FIXTURE.parent / "preserved.json").read_bytes())["protected_sha256"]
        authorization = json.loads((FIXTURE.parents[1] / "ask/preserved.json").read_bytes())
        expected["docs/authorization.md"] = authorization["protected_sha256"]["docs/authorization.md"]
        cases = (
            ("router-writes", "template/.agents/skills/knowledge/SKILL.md", "The router may write files."),
            ("auto-workflows", "template/.agents/skills/knowledge/SKILL.md", "Automatically invoke other workflows."),
            ("application-code", "docs/knowledge.md", "Application code changes are authorized."),
            ("authorization", "docs/authorization.md", "Authorization restrictions are removed."),
            ("no-change", "docs/knowledge-init.md", "NO_CHANGE may modify existing files."),
        )
        for name, rel, mutation in cases:
            with self.subTest(mutation=name):
                original = (PRODUCT / rel).read_bytes()
                digest = expected[rel]
                self.assertEqual(digest, hashlib.sha256(original).hexdigest(), rel)
                case = root / name
                case.mkdir()
                (case / "original").write_bytes(original)
                changed = original + ("\n" + mutation + "\n").encode("utf-8")
                (case / "mutated").write_bytes(changed)
                observed = hashlib.sha256((case / "mutated").read_bytes()).hexdigest()
                write_json(case / "result.json", {"source": rel, "expected_sha256": digest,
                           "observed_sha256": observed, "status": "PASS" if observed != digest else "FAIL"})
                self.assertNotEqual(digest, observed, rel)

    def test_contract_and_skill(self):
        root = PRODUCT / "template/.agents/skills"
        skill = (root / "knowledge-init/SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"\A---\nname: knowledge-init\ndescription: (\"[^\n]+\")\n---\n", skill)
        self.assertIsNotNone(match)
        self.assertIn("Initialize", json.loads(match[1]))
        for name in ("knowledge", "knowledge-init", "authorization"):
            self.assertIn("../_contracts/" + name + ".md", skill)
        common = (PRODUCT / "docs/knowledge.md").read_text(encoding="utf-8")
        init = (PRODUCT / "docs/knowledge-init.md").read_text(encoding="utf-8")
        for clause in ("user declarations", "inferences", "missing information", "junctions", "Do not silently overwrite", "not a sandbox", "NEEDS_INPUT"):
            self.assertIn(clause, common)
        for clause in ("empty or partial", "conflicts", "NO_CHANGE", "PARTIAL", "BLOCKED", "inventory alone", "Reread"):
            self.assertIn(clause, init)
        for name in ("knowledge.md", "knowledge-init.md", "workflows.md"):
            self.assertEqual((PRODUCT / "docs" / name).read_bytes(), (root / "_contracts" / name).read_bytes())
if __name__ == "__main__":
    unittest.main()
