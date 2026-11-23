"""Independent gold mutations and static candidate preservation checks."""
import copy
import json
import os
from pathlib import Path
import unittest

from runner.refine_implement_oracle import evaluate
from runner.refine_implement_windows import decode_capture, digest

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = os.environ.get("CODEX_PRODUCT_ROOT")
FIXTURES = ROOT / "fixtures/refine-implement"


class RefineImplementPreservation(unittest.TestCase):
    @unittest.skipUnless(PRODUCT, "NOT_RUN: select Product checkout")
    def test_product_asset_preservation(self):
        product = Path(PRODUCT).resolve()
        frozen = json.loads((FIXTURES / "preserved.json").read_bytes())
        actual = sorted(path.relative_to(product).as_posix()
                        for path in (product / "template").rglob("*") if path.is_file())
        self.assertEqual(frozen["template_inventory"], actual)
        for rel, expected in frozen["protected_sha256"].items():
            with self.subTest(path=rel):
                self.assertEqual(expected, digest((product / rel).read_bytes()))
        self.assertEqual([], list((product / "template/.codex/agents").glob("*.toml")))


def read_json(path):
    return json.loads(path.read_bytes())


class RefineImplementOracleReplay(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("REFINE_IMPLEMENT_RESULT_ROOT"), "NOT_RUN: select a sealed simulator result")
    def test_rejects_tampered_readiness_and_handoff_evidence(self):
        run = Path(os.environ["REFINE_IMPLEMENT_RESULT_ROOT"]).resolve()
        manifest = read_json(run / "evidence-sha256.json")
        for rel, expected in manifest.items():
            self.assertEqual(expected, digest((run / rel).read_bytes()), rel)
        definitions = {row["id"]: row for row in read_json(FIXTURES / "cases.json")["cases"]}

        def replay_inputs(name):
            folder = run / name
            return (definitions[name], *(read_json(folder / (key + ".json"))
                     for key in ("input", "observations", "before", "after")))

        ready = replay_inputs("ready")
        evaluate(*ready)
        spoofed = copy.deepcopy(ready)
        spoofed[2]["contract"]["markdown"] = spoofed[2]["contract"]["markdown"].replace("WI-101@1", "WI-101@0")
        with self.assertRaisesRegex(AssertionError, "provenance"):
            evaluate(*spoofed)
        wrong_value = copy.deepcopy(ready)
        wrong_value[2]["contract"]["markdown"] = wrong_value[2]["contract"]["markdown"].replace("state=enabled", "state=wrong")
        with self.assertRaisesRegex(AssertionError, "Markdown field"):
            evaluate(*wrong_value)

        handoff = replay_inputs("handoff")
        evaluate(*handoff)
        excess = copy.deepcopy(handoff)
        excess[4]["entries"]["sentinel.txt"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(AssertionError, "persistent delta"):
            evaluate(*excess)
        false_report = copy.deepcopy(handoff)
        false_report[2]["report"]["delta"].append("sentinel.txt")
        with self.assertRaisesRegex(AssertionError, "report differs"):
            evaluate(*false_report)
        reordered = copy.deepcopy(handoff)
        reordered[2]["events"].reverse()
        with self.assertRaisesRegex(AssertionError, "reordered"):
            evaluate(*reordered)

        early_report = copy.deepcopy(replay_inputs("verification_fail"))
        events = early_report[2]["events"]
        self.assertEqual(["checkpoint", "write", "verification", "report"], [event["type"] for event in events])
        events[1], events[2] = events[2], events[1]
        for sequence, event in enumerate(events, 1):
            event["sequence"] = sequence
            event["monotonic_ns"] = sequence
        with self.assertRaisesRegex(AssertionError, "verification did not follow"):
            evaluate(*early_report)

    def test_missing_completion_seals_fail_closed(self):
        process = {"exit_code": 0, "stop_reason": None, "job_drained": True, "evidence_redacted": False}
        partial = (b'{"event":"start","sequence":1,"actor":"simulated"}\n'
                   b'{"event":"write","sequence":2,"path":"app/settings.txt","value":"x"}\n')
        self.assertEqual((None, "missing-terminal"), decode_capture(partial, process))
        self.assertEqual((None, "partial-line"), decode_capture(partial + b'{"event":"end","sequence":3}', process))
        self.assertEqual((None, "sequence"), decode_capture(partial + b'{"event":"end","sequence":4}\n', process))
        malformed = partial + b'{"event":"report","sequence":3}\n{"event":"end","sequence":4}\n'
        self.assertEqual((None, "schema"), decode_capture(malformed, process))
        self.assertEqual((None, "transport"), decode_capture(malformed, dict(process, exit_code=1)))
        valid = partial + b'{"event":"end","sequence":3}\n'
        self.assertEqual(["write"], [frame["event"] for frame in decode_capture(valid, process)[0]])
