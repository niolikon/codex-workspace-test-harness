"""Synthetic TTL update assertions for current, superseded and cited facts."""
import unittest

from runner import knowledge_update_document as oracle
from runner.paths import FIXTURES

DOCUMENT = (
    b"# Service\n\n## Current\nTTL: 90 s (v2).\n\n"
    b"## History\nTTL: 60 s (v1, superseded by v2).\n\n"
    b"## Source\n[Approved v2](../documents/spec-v2.md#L3-L4)\n"
)


class KnowledgeUpdateDocumentTests(unittest.TestCase):
    def test_document_requires_current_and_superseded_ttl(self):
        fixture = FIXTURES / "knowledge-update/document"
        source = (fixture / "spec-v2.md").read_bytes()
        self.assertEqual(oracle.GOLD_BASELINE, (fixture / "service.md").read_bytes())
        self.assertEqual(oracle.GOLD_SOURCE, source)
        observed = oracle.observation("source-read", oracle.SOURCE, source)
        for document, semantic, citations in (
            (DOCUMENT, "PASS", "PASS"),
            (DOCUMENT.replace(b"90 s", b"60 s"), "FAIL", "FAIL"),
            (DOCUMENT.replace(b"60 s", b"50 s"), "FAIL", "PASS"),
            (DOCUMENT.replace(b"L3-L4", b"L1-L1"), "PASS", "FAIL"),
            (DOCUMENT.replace(b"L3-L4", b"L3-L3"), "PASS", "FAIL"),
        ):
            with self.subTest(document=document):
                result = oracle.document_checks(document, source, observed)
                self.assertEqual((semantic, citations), tuple(row["status"] for row in result))

    def test_readback_matches_exact_final_bytes(self):
        event = oracle.observation("readback", oracle.TARGET, DOCUMENT)
        event["content"] = DOCUMENT.decode()
        self.assertEqual("PASS", oracle.readback_check(event, DOCUMENT)["status"])
        self.assertEqual("FAIL", oracle.readback_check(event, DOCUMENT + b"\n")["status"])
