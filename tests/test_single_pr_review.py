"""Adversarial tests of the Harness observation and binding checks, using synthetic inputs."""
from copy import deepcopy
from pathlib import Path
from time import monotonic, time_ns
from unittest.mock import patch
import os
import unittest

from runner import single_pr_review as p8


@unittest.skipUnless(os.environ.get("CODEX_PRODUCT_ROOT"), "NOT_RUN: select Product")
class SinglePrReviewExamples(unittest.TestCase):
    def test_relocated_example_and_review_contract(self):
        p8.check_review_example(Path(os.environ["CODEX_PRODUCT_ROOT"]).resolve())


class SinglePrReview(unittest.TestCase):
    def test_sealed_gold_covers_every_required_variant(self):
        cases, expected, _, _ = p8.sealed_inputs()
        self.assertEqual(len(cases["cases"]), 29)
        for case in cases["cases"]:
            gold = expected["cases"][case["id"]]
            if gold["gate"] == "denied":
                self.assertTrue(gold["e0"])
                self.assertEqual(gold["writer_calls"], 0)
                self.assertIn(case["mutation"], p8.REASONS)

    def test_duplicate_receipt_key_is_rejected_even_if_values_match(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            p8.load(b'{"decision":"allow","decision":"allow"}')

    def test_forged_link_and_false_success_are_rejected(self):
        gold = {"gate": "denied", "review": None}
        result = {"gate": "denied", "reason": "SELECTION_UNBOUND", "review_state": None,
                  "published_path": None}
        p8.expect_result(result, gold, "select_other_item_pr")
        for forged in (dict(result, gate="allowed"), dict(result, published_path="forged"),
                       dict(result, reason="CONSENT_DENIED")):
            with self.subTest(forged=forged), self.assertRaises(ValueError):
                p8.expect_result(forged, gold, "select_other_item_pr")

    def test_forged_receipt_is_rejected_even_when_repinned(self):
        preview = {k: "fixture" for k in p8.RECEIPT_BINDING}
        preview.update(generation=1, sha="a" * 64)
        valid = {k: preview[k] for k in p8.RECEIPT_BINDING}
        valid.update(schema_version=1, receipt_id="r1", type="simulated", decision="allow",
                     preview_sha256=preview["sha"])
        raw = p8.review_validation.json_bytes(valid)
        self.assertEqual(p8.check_receipt(raw, p8.digest(raw), preview), valid)
        for forged in (dict(valid, preview_sha256="b" * 64), dict(valid, provenance="forged"),
                       dict(valid, generation=True), dict(valid, decision="implicit")):
            raw = p8.review_validation.json_bytes(forged)
            with self.subTest(forged=forged), self.assertRaises(ValueError):
                p8.check_receipt(raw, p8.digest(raw), preview)

    def test_forged_preview_detected_before_dom(self):
        template = {"pr_id": "12", "generation": 1}
        for forged in (dict(template, pr_id="13"), dict(template, generation=True),
                       dict(template, extra="hidden")):
            with self.subTest(forged=forged), self.assertRaisesRegex(ValueError, "preview"):
                p8.check_preview(p8.review_validation.json_bytes(forged), template, b"{}", b"")

    def test_invalid_html_does_not_pass_even_when_preview_fields_match(self):
        template = {"pr_id": "12"}
        with self.assertRaises(AssertionError):
            p8.check_preview(p8.review_validation.json_bytes(template), template, b"{}\n",
                             b"<!doctype html>\n<html>forged</html>\n")

    def test_required_acquisitions_and_effect_order(self):
        root = p8.ROOT / "inputs"
        witness = {name: {"path": f"{name}.json"} for name in ("manifest", "candidate", "diff")}
        witness["sources"] = [{"path": "source-a"}, {"path": "source-b"}]
        trace = [{"boundary": kind, "name": str(root / path)} for kind, path in (
            ("manifest", "manifest.json"), ("candidate", "candidate.json"), ("diff", "diff.json"),
            ("source", "source-a"), ("source", "source-b"), ("claim", "receipt"), ("writer", "view.json"),
        )]
        effects = ("claim", "writer")
        p8.check_trace(trace, root, witness, "full", effects)
        p8.check_trace(trace[:1], root, witness, "manifest")
        p8.check_trace([], root, witness, "none")
        for forged in ([trace[-2:]] + [trace[:i] + trace[i+1:] for i in range(5)] + [
                trace[:4] + [trace[3]] + trace[5:],
                [*trace[:3], *trace[5:], *trace[3:5]],
                [*trace[:5], *trace[:4:-1]],
                [dict(trace[0], name=str(root / "other-manifest.json")), *trace[1:]],
        ]):
            with self.subTest(forged=forged), self.assertRaises(ValueError):
                p8.check_trace(forged, root, witness, "full", effects)
        with self.assertRaises(ValueError):
            p8.check_trace([], root, witness, "manifest")
        with self.assertRaises(ValueError):
            p8.check_trace(trace[:1], root, witness, "none")

    def test_forged_staging_is_rejected(self):
        state = {"inputs": {}, "control": {}, "unrelated": {},
                 "ledger": {}, "workspace": {"sentinel": {"sha256": "fixed"}}}
        p8.preservation(state, state, deny=True)
        for location in ("workspace", "ledger", "inputs", "unrelated"):
            forged = deepcopy(state)
            forged[location][".staging"] = {"directory": True}
            with self.subTest(location=location), self.assertRaises(ValueError):
                p8.preservation(state, forged, deny=True)

    def test_supervisor_exit_zero_without_drain_is_insufficient(self):
        result = dict(launched=True, stop_reason=None, job_drained=False,
                      evidence_redacted=False, stream_limit_reached=False, exit_code=0)
        with self.assertRaises(ValueError):
            p8.check_termination(result, {0})

    @unittest.skipUnless("SINGLE_PR_REVIEW_TEST_ROOT" in os.environ, "NOT_RUN: set SINGLE_PR_REVIEW_TEST_ROOT to a fresh external directory")
    def test_byte_identical_pair_in_wrong_directory_is_rejected(self):
        root = Path(os.environ["SINGLE_PR_REVIEW_TEST_ROOT"]) / "placement"
        destination = root / "requested"
        canonical_parent = destination / "session_data" / "reviews"
        impostor_parent = root / "other" / "session_data" / "reviews"
        canonical_parent.mkdir(parents=True)
        impostor_parent.mkdir(parents=True)
        before = p8.inventory(root)
        raw, html = b"{}\n", b"same report\n"
        leaf = "rv02-review--20261121T1234560000000Z"
        right = canonical_parent / leaf
        wrong = impostor_parent / leaf
        for pair in (wrong, right):
            pair.mkdir()
            (pair / "review.json").write_bytes(raw)
            (pair / "report.html").write_bytes(html)
        after = p8.inventory(root)
        for pair, other in ((right, wrong), (wrong, right)):
            prefix = other.relative_to(root).as_posix()
            single = {name: value for name, value in after.items()
                      if name != prefix and not name.startswith(prefix + "/")}
            with patch.object(p8.dom, "expected"), patch.object(p8.dom, "pair",
                    return_value=(html, None, None)) as project:
                if pair == right:
                    p8.check_pair(root, destination, str(pair), before, single, raw, html)
                    project.assert_called_once()
                    with self.assertRaisesRegex(ValueError, "reported publication path"):
                        p8.check_pair(root, destination, str(wrong), before, single, raw, html)
                else:
                    with self.assertRaisesRegex(ValueError, "physical pair"):
                        p8.check_pair(root, destination, str(pair), before, single, raw, html)
                    project.assert_not_called()

    @unittest.skipUnless("SINGLE_PR_REVIEW_TEST_ROOT" in os.environ, "NOT_RUN: set SINGLE_PR_REVIEW_TEST_ROOT to a fresh external directory")
    def test_backdated_receipt_preserves_binding_and_issue_order(self):
        root = Path(os.environ["SINGLE_PR_REVIEW_TEST_ROOT"]) / "receipt-mtime"
        definitions, _, _, _ = p8.sealed_inputs()
        trial = p8.Trial(root, root, root, None, 0, definitions, "backdate_bound_receipt", True)
        trial.preview_observed_us = int(monotonic() * 1_000_000)
        trial.preview_mtime_ns = time_ns() - 2_000_000_000
        preview = trial.preview_expected("a" * 32, "complete", b"{}\n", b"report\n")
        preview["sha"] = "b" * 64
        pin = trial.receipt(preview)
        raw = (trial.control / "receipt.json").read_bytes()
        self.assertEqual(p8.check_receipt(raw, pin, preview)["decision"], "allow")
        witness = p8.load_observation(trial.runlog / "simulator-initial.json")
        self.assertEqual(witness["test"], "synthetic_early_mtime")
        self.assertLess(witness["file_mtime_ns"], witness["preview_mtime_ns"])
        self.assertGreater(witness["initial_mtime_ns"], witness["preview_mtime_ns"])
        self.assertGreaterEqual(witness["observed_monotonic_us"], trial.preview_observed_us)

    @unittest.skipUnless("SINGLE_PR_REVIEW_TEST_ROOT" in os.environ, "NOT_RUN: set SINGLE_PR_REVIEW_TEST_ROOT to a fresh external directory")
    def test_seal_rereads_real_manifest_over_one_mib(self):
        root = Path(os.environ["SINGLE_PR_REVIEW_TEST_ROOT"]) / "manifest-large"
        root.mkdir()
        names = [f"artifact-{number:04d}-{'x' * 100}.bin" for number in range(5600)]
        for name in names:
            (root / name).touch(exist_ok=False)
        expected = {"bytes": 0, "sha256": p8.digest(b"")}
        pin = p8.seal(root)
        manifest = root / "manifest.sha256.json"
        raw = manifest.read_bytes()
        self.assertGreater(len(raw), p8.MIB)
        self.assertLessEqual(len(raw), p8.EVIDENCE_FILE_LIMIT)
        self.assertEqual(pin, p8.digest(raw))
        self.assertEqual(p8.load(raw, size_limit=p8.EVIDENCE_FILE_LIMIT),
                         [[name, expected] for name in names])
        with self.assertRaisesRegex(ValueError, "file exceeds byte limit"):
            p8.bounded_read(manifest)
        with self.assertRaisesRegex(ValueError, "invalid JSON framing"):
            p8.load(raw)
        with self.assertRaisesRegex(ValueError, "view byte limit"):
            p8.review_validation.load_view(raw)

    @unittest.skipUnless("SINGLE_PR_REVIEW_TEST_ROOT" in os.environ, "NOT_RUN: set SINGLE_PR_REVIEW_TEST_ROOT to a fresh external directory")
    def test_seal_rejects_oversized_index_before_reading(self):
        root = Path(os.environ["SINGLE_PR_REVIEW_TEST_ROOT"]) / "manifest-oversize"
        root.mkdir()

        def write_oversize(path, _rows):
            with path.open("xb") as stream:
                stream.truncate(p8.EVIDENCE_FILE_LIMIT + 1)

        with patch.object(p8, "write", side_effect=write_oversize), patch.object(
                p8, "bounded_read", side_effect=AssertionError("oversized index must not be read")) as read:
            with self.assertRaisesRegex(ValueError, "manifest exceeds file byte limit"):
                p8.seal(root)
            read.assert_not_called()
        self.assertEqual((root / "manifest.sha256.json").stat().st_size, p8.EVIDENCE_FILE_LIMIT + 1)

    @unittest.skipUnless("SINGLE_PR_REVIEW_TEST_ROOT" in os.environ, "NOT_RUN: set SINGLE_PR_REVIEW_TEST_ROOT to a fresh external directory")
    def test_case_evidence_budget_is_cumulative_without_reopening_closed_cases(self):
        root = Path(os.environ["SINGLE_PR_REVIEW_TEST_ROOT"]) / "accounting"
        first = (root / "00-seam", root / "00-cli")
        second = (root / "01-seam", root / "01-cli")
        for directory in (*first, *second):
            directory.mkdir(parents=True)
            (directory / "stdout.bin").write_bytes(b"xx")
        with patch.object(p8, "inventory", wraps=p8.inventory) as inventory:
            spent = p8.account_case_evidence(first, 2, 1, byte_limit=10)
            self.assertEqual(spent, (6, 5))
            self.assertEqual(p8.account_case_evidence(second, *spent, byte_limit=10), (10, 9))
            self.assertEqual([call.args[0] for call in inventory.call_args_list], [*first, *second])
        with self.assertRaisesRegex(ValueError, "aggregate byte budget"):
            p8.account_case_evidence(second, *spent, byte_limit=9)

    @unittest.skipUnless("SINGLE_PR_REVIEW_TEST_ROOT" in os.environ, "NOT_RUN: set SINGLE_PR_REVIEW_TEST_ROOT to a fresh external directory")
    def test_materialization_is_disjoint_and_synthetic(self):
        root = Path(os.environ["SINGLE_PR_REVIEW_TEST_ROOT"]) / "material"
        definitions, _, _, _ = p8.sealed_inputs()
        trial = p8.Trial(root, root, root, None, 0, definitions, "none", True)
        source = trial.inputs / trial.selected[0]["sources"][0]["path"]
        self.assertTrue(source.read_bytes().startswith(b"SAMPLE-17\n"))
        self.assertEqual(trial.selected[0]["pr_id"], "12")
        self.assertEqual(trial.other[0]["pr_id"], "13")
        for other in (trial.other, trial.unrelated):
            self.assertFalse({row["path"] for row in trial.selected[0]["sources"]} &
                             {row["path"] for row in other[0]["sources"]})
        SHA = p8.digest(source.read_bytes())
        self.assertEqual(SHA, trial.material[0]["scope"]["work_item"]["sha256"])


if __name__ == "__main__":
    unittest.main()
