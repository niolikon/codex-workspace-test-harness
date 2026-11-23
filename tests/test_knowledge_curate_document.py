"""Exercise the curation Markdown grammar and citation requirements."""
import unittest

from runner import knowledge_curate_document
from runner.paths import FIXTURES, sha


class KnowledgeCurateDocumentTests(unittest.TestCase):
    def setUp(self):
        self.sources = {
            path: (FIXTURES / 'knowledge-curate/document' / path.rsplit('/', 1)[1]).read_bytes()
            for path in knowledge_curate_document.SOURCES
        }
        self.operations = '[Operations](../documents/operations.md#L3)'
        self.service = '[Service spec](../documents/service-spec.md#L3)'

    def check_document(self, references):
        document = ('# Cache TTL\n\n- TTL: 60 s. Applicability: production. Origins: '
                    + references + '.\n').encode('utf-8')
        semantic, citations = knowledge_curate_document.document_checks(document, self.sources, self.sources, distinct=False, exact_ranges=False)
        return semantic['status'], citations['status']

    def test_complete_links_with_either_separator(self):
        operations = self.operations.replace('[Operations]', '[Operations, verified; primary]')
        for separator in (', ', '; ', ',', ';'):
            with self.subTest(separator=separator):
                self.assertEqual(self.check_document(operations + separator + self.service), ('PASS', 'PASS'))

    def test_punctuation_does_not_admit_incomplete_links_or_junk(self):
        for tail in (',', '; [Service spec]', ', see ' + self.service, ' ' + self.service):
            with self.subTest(tail=tail):
                self.assertEqual(self.check_document(self.operations + tail), ('FAIL', 'FAIL'))

    def test_missing_duplicate_and_staging_only_origins_fail_citations(self):
        for references in (
            self.operations,
            self.operations + ', ' + self.operations + '; ' + self.service,
            self.operations + ', ' + self.service.replace('#L3', '#L4'),
        ):
            with self.subTest(references=references):
                self.assertEqual(self.check_document(references), ('PASS', 'FAIL'))

    def test_distinct_applicability_must_remain_separate(self):
        baseline = (FIXTURES / 'knowledge-curate/document' / 'a-distinct.md').read_bytes()
        semantic, citations = knowledge_curate_document.document_checks(baseline, self.sources, self.sources, distinct=True)
        self.assertEqual((semantic['status'], citations['status']), ('PASS', 'PASS'))
        candidate = (b'# Cache TTL\n\n- TTL: 60 s. Applicability: production. Origins: '
                     + (self.operations + ', ' + self.service.replace('#L3', '#L3-L4')).encode('utf-8')
                     + b'.\n')
        semantic, citations = knowledge_curate_document.document_checks(candidate, self.sources, self.sources, distinct=True)
        self.assertEqual((semantic['status'], citations['status']), ('FAIL', 'PASS'))


    def test_deduplication_preserves_unrelated_document_and_checks_readback(self):
        fixture = FIXTURES / "knowledge-curate/document"
        target, sentinel = knowledge_curate_document.TARGET, knowledge_curate_document.SENTINEL
        before_files = {target: sha((fixture / "a.md").read_bytes()),
                        sentinel: sha((fixture / "keep.md").read_bytes())}
        document = ("# Cache TTL\n\n- TTL: 60 s. Applicability: production. Origins: "
                    + self.operations + ", " + self.service.replace("#L3", "#L3-L4") + ".\n").encode()
        after_files = {**before_files, target: sha(document)}
        state = {"directories": ["knowledge-base"], "metadata": {
            target: {"attributes": 0}, sentinel: {"attributes": 0}}}
        before = {**state, "files": before_files}
        after = {**state, "files": after_files}
        delta = knowledge_curate_document.workspace_delta
        self.assertEqual("PASS", delta(before, after)["status"])
        self.assertEqual("FAIL", delta(before, {**after, "files": {**after_files, sentinel: "tampered"}})["status"])
        event = knowledge_curate_document.observation("readback", target, document)
        event["content"] = document.decode()
        self.assertEqual("PASS", knowledge_curate_document.readback_check(event, document)["status"])
        self.assertEqual("FAIL", knowledge_curate_document.readback_check(event, document + b"\n")["status"])
