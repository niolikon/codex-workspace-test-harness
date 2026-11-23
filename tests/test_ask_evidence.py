"""Independent Ask answer checks and synthetic trace falsifications."""
import json
import os
import sys
import unittest

from runner import ask_oracle as oracle
from runner.paths import FIXTURES, ROOT, sha
from runner.windows_process import controlled_env, run_process


PROCESS = dict(launched=True, job_drained=True, stop_reason=None, exit_code=0,
               evidence_redacted=False, stream_limit_reached=False)
TERMINAL = {"type": "turn.completed", "usage": dict.fromkeys(oracle.USAGE_FIELDS, 1)}


def encode(frames):
    return b"".join(json.dumps(frame).encode() + b"\n" for frame in frames)


class AskEvidenceTests(unittest.TestCase):
    def setUp(self):
        inputs = json.loads((FIXTURES / "ask/source-inputs.json").read_bytes())
        self.sources = inputs["files"]
        self.answer = {"ttl_seconds": 60, "owner": "Team Blu", "citations": {
            "ttl_seconds": [{"path": oracle.CODE, "line": 1}],
            "owner": [{"path": oracle.SPEC, "line": 3}],
        }}

    def test_claims_and_citations(self):
        for field, bad, expected in (
            (None, None, set()), ("owner", "wrong", {"owner", "citations"}),
            ("ttl_seconds", 90, {"ttl_seconds", "citations"}),
            ("citations", {"ttl_seconds": [{"path": oracle.SPEC, "line": 1}],
                           "owner": self.answer["citations"]["owner"]}, {"citations"}),
        ):
            with self.subTest(field=field):
                answer = {**self.answer, field: bad} if field else self.answer
                findings = oracle.answer_checks(json.dumps(answer), self.sources)
                self.assertEqual(expected, {key for key, row in findings.items() if row["status"] == "FAIL"})

    def test_missing_unknown_and_malformed_terminal_are_distinct(self):
        self.assertEqual([], oracle.observe(b"")["terminal"])
        result = oracle.observe(b'{"type":"future_event"}\n{"type":"turn.completed"}\n{"type":\n')
        self.assertEqual([1], result["unknown"])
        self.assertEqual([2, 3], result["malformed"])
        self.assertEqual([], result["terminal"])

    def test_completion_and_read_evidence_are_independent(self):
        start = {"type": "turn.started"}
        answer = {"type": "item.completed", "item": {"id": "answer", "type": "agent_message",
                                                    "text": json.dumps(self.answer)}}
        files = {path: sha(text.encode()) for path, text in self.sources.items()}
        reads = [{"type": "harness.simulated_read", "path": path, "sha256": digest}
                 for path, digest in files.items()]
        for frames, completion, tools in (
            ([start, *reads, answer, TERMINAL], "PASS", "PASS"),
            ([start, answer, *reads, TERMINAL], "PASS", "FAIL"),
            ([start, answer, TERMINAL], "PASS", "BLOCKED"),
            ([start, *reads, answer, {"type": "future_event"}], "BLOCKED", "PASS"),
        ):
            raw = encode(frames)
            observed = oracle.observe(raw, simulated=True)
            self.assertEqual(completion, oracle.completion(PROCESS, observed, raw)["status"])
            self.assertEqual(tools, oracle.tool_use(observed, {"files": files})["status"])

    def test_completed_reads_require_matching_output_and_command_pairs(self):
        frozen = {path: self.sources.get(path, "synthetic text\n").encode() for path in oracle.READ_PATHS}
        groups = [oracle.READ_PATHS[:3], oracle.READ_PATHS[3:]]
        for variant, expected in (("correct", "PASS"), ("failed", "FAIL"),
                                  ("wrong-content", "FAIL"), ("no-output", "BLOCKED"),
                                  ("no-start", "BLOCKED"), ("no-completion", "BLOCKED"),
                                  ("boolean-exit", "BLOCKED")):
            with self.subTest(variant=variant):
                frames = [{"type": "turn.started"}]
                for index, paths in enumerate(groups):
                    item = {"id": str(index), "type": "command_execution",
                            "command": oracle.READ_PREFIX + " ".join(paths) + "'"}
                    rows = "".join(f"{path}:{number}:{line}\n" for path in paths
                                   for number, line in enumerate(frozen[path].decode().splitlines(), 1))
                    completed = dict(item, status="completed", exit_code=0, aggregated_output=rows)
                    if index == 0:
                        if variant == "failed":
                            completed.update(status="failed", exit_code=1)
                        elif variant == "no-output":
                            del completed["aggregated_output"]
                        elif variant == "boolean-exit":
                            completed["exit_code"] = False
                        elif variant == "wrong-content":
                            completed["aggregated_output"] = rows.replace(":1:", ":99:")
                    if index or variant != "no-start":
                        frames.append({"type": "item.started", "item": dict(item, status="in_progress")})
                    if index or variant != "no-completion":
                        frames.append({"type": "item.completed", "item": completed})
                raw = encode([*frames, TERMINAL])
                observed = oracle.verify_reads(PROCESS, oracle.observe(raw), raw, frozen)
                self.assertEqual(expected, observed["status"])
                self.assertEqual("PASS", observed["files"][oracle.CODE]["status"])


@unittest.skipUnless(os.name == "nt", "SKIP: Windows Job Object required")
class WindowsCaptureTests(unittest.TestCase):
    def test_capture_redacts_the_effective_credential(self):
        marker = "offline-capture-credential"
        script = ("import os,sys; credential = os.environ['CODEX_API_KEY']; "
                  "print(credential); sys.stderr.write(credential[:12])")
        env = {**controlled_env(), "CODEX_API_KEY": marker}
        process, stdout, stderr = run_process([sys.executable, "-I", "-B", "-c", script],
                                              ROOT, env, 10, 5)
        self.assertIsNone(process["stop_reason"])
        self.assertEqual(0, process["exit_code"])
        self.assertTrue(process["job_drained"])
        self.assertTrue(process["evidence_redacted"])
        self.assertEqual(b"[REDACTED]", stdout.strip())
        self.assertEqual(b"[REDACTED]", stderr)
