"""Knowledge initialization document and synthetic trace assertions."""
import json
import unittest

from runner import knowledge_init_document as oracle
from runner.oracles import aggregate
from runner.ask_oracle import READ_PREFIX, observe
from runner.paths import FIXTURES, ROOT

DOCUMENT = (
    b'# Operational notes\n\nService settings from the [spec](../documents/spec.md).\n'
    b'**TTL** is 60 seconds. **Owner** is Team Blu.\n'
    b'Other settings are out of scope.\n'
)
PROCESS = dict(launched=True, job_drained=True, stop_reason=None, exit_code=0,
               evidence_redacted=False, stream_limit_reached=False)


def trace(variant='complete'):
    write = {'id': 'write', 'type': 'file_change',
             'changes': [{'kind': 'add', 'path': oracle.TARGET}]}
    if variant == 'transient-extra':
        write['changes'].append({'kind': 'delete', 'path': 'notes/other.md'})
    command = READ_PREFIX.replace('rg -n ', 'rg -n -H ') + oracle.TARGET + "'"
    rows = [f'{oracle.TARGET}:{number}:{line}\n'
            for number, line in enumerate(DOCUMENT.decode().splitlines(), 1)]
    if variant == 'wrong-output':
        rows[3] = rows[3].replace('60 seconds', '90 seconds')
    if variant == 'partial-output':
        rows.pop()
    read = {'id': 'read', 'type': 'command_execution', 'command': command}
    write_frames = [
        {'type': 'item.started', 'item': {**write, 'status': 'in_progress'}},
        {'type': 'item.completed', 'item': {**write, 'status': 'completed'}},
    ]
    read_frames = [
        {'type': 'item.started', 'item': {**read, 'status': 'in_progress'}},
        {'type': 'item.completed', 'item': {**read, 'status': 'completed', 'exit_code': 0,
                                          'aggregated_output': ''.join(rows)}},
    ]
    events = [
        {'type': 'turn.started'},
        *(read_frames if variant == 'read-first' else []),
        *(write_frames if variant != 'no-write' else []),
        *(read_frames if variant not in ('read-first', 'no-read') else []),
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'id': 'message',
                                          'text': 'COMPLETED. Readback done.'}},
        {'type': 'turn.completed', 'usage': {
            'input_tokens': 1, 'output_tokens': 1, 'cached_input_tokens': 0,
        }},
    ]
    raw = b''.join(json.dumps(event).encode() + b'\n' for event in events)
    return raw[:-1] if variant == 'truncated' else raw


class KnowledgeInitDocumentTests(unittest.TestCase):
    def test_document_fixture_matches_independent_source(self):
        raw = (FIXTURES / 'knowledge-init/document/spec.md').read_bytes()
        self.assertTrue(oracle.source_matches(raw))

    def test_independent_markdown_criteria(self):
        self.assertEqual({'PASS'}, {item['status'] for item in oracle.document_checks(DOCUMENT).values()})
        for criterion, old, new in (
            ('ttl', b'60 seconds', b'60 ms'),
            ('owner', b'Team Blu', b'Team Red'),
            ('scope', b'Service settings', b'Unrelated settings'),
            ('limits', b'Other settings are out of scope.', b'All settings are covered.'),
            ('source_link', b'../documents/spec.md', b'../documents/missing.md'),
        ):
            with self.subTest(criterion=criterion):
                checks = oracle.document_checks(DOCUMENT.replace(old, new))
                self.assertEqual('FAIL', checks[criterion]['status'])
        fenced = b'~~~markdown\n' + DOCUMENT + b'~~~\n'
        checks = oracle.document_checks(fenced)
        for criterion in ('ttl', 'owner', 'scope', 'limits', 'source_link'):
            self.assertEqual('FAIL', checks[criterion]['status'], criterion)

    def test_markdown_separators_and_heading_ancestry(self):
        for separator in (':', '=', '|', '-', '–', '—'):
            with self.subTest(separator=separator):
                candidate = (
                    '# Servizio\n\n## Configurazione e responsabilità\n\n'
                    f'- **TTL** {separator} 60 s.\n'
                    f'- **Team responsabile (owner)** {separator} Team Blu.\n\n'
                    'Fonte: [specifica del servizio](../documents/spec.md), righe 2–3.\n\n'
                    '## Limiti delle informazioni\n\n'
                    'La specifica non indica a cosa si applichi il TTL. '
                    'Il comportamento a runtime resta da verificare.\n'
                ).encode()
                self.assertEqual({'PASS'}, {item['status'] for item in
                                            oracle.document_checks(candidate).values()})
        sibling = candidate.replace(b'- **TTL**', b'## Observed facts\n\n- **TTL**')
        self.assertEqual('FAIL', oracle.document_checks(sibling)['scope']['status'])

    def test_explicit_source_line_references(self):
        for reference, expected in (
            ('', 'PASS'),
            (', righe 2–3', 'PASS'),
            (', line 3', 'PASS'),
            (', riga 0', 'FAIL'),
            (', lines 2-4', 'FAIL'),
            (', righe 900–901', 'FAIL'),
            (', lines 3–2', 'FAIL'),
        ):
            with self.subTest(reference=reference):
                citation = f'\nSource: [spec](../documents/spec.md){reference}.\n'.encode()
                checks = oracle.document_checks(DOCUMENT + citation)
                self.assertEqual(expected, checks['source_link']['status'])
                self.assertEqual(expected, aggregate([(check, True) for check in checks.values()])['status'])

    def test_summary_citations_support_each_uncited_fact(self):
        document = (
            '# Servizio\n\n## Configurazione e responsabilità\n\n'
            '- **TTL** — 60 s.\n'
            '- **Team responsabile (owner)** — Team Blu.\n\n'
            'Fonte: [specifica del servizio](../documents/spec.md){reference}.\n'
            '{extra}\n'
            '## Limiti delle informazioni\n\n'
            'La specifica non indica a cosa si applichi il TTL.\n'
        )
        for reference, extra, expected in (
            ('', '', 'PASS'),
            (', righe 2–3', '', 'PASS'),
            (', righe 1–3', '', 'PASS'),
            (', riga 1', '', 'FAIL'),
            (', riga 2', '', 'FAIL'),
            (', riga 3', '', 'FAIL'),
            (', righe 900–901', '', 'FAIL'),
            (', riga 1', '\nFonte: [spec](../documents/spec.md), righe 2–3.\n', 'FAIL'),
        ):
            with self.subTest(reference=reference, extra=extra):
                candidate = document.format(reference=reference, extra=extra).encode()
                checks = oracle.document_checks(candidate)
                self.assertEqual('PASS', checks['ttl']['status'])
                self.assertEqual('PASS', checks['owner']['status'])
                self.assertEqual(expected, checks['source_link']['status'])
                self.assertEqual(expected, aggregate([(check, True) for check in checks.values()])['status'])

    def test_fact_local_citations_support_their_own_fact(self):
        link = '[spec](../documents/spec.md)'
        for ttl_ref, owner_ref, expected in (
            (f'({link}, line 2)', f'{link}, line 3', 'PASS'),
            ('[spec, line 2](../documents/spec.md)', f'{link}, line 3', 'PASS'),
            (f'({link}, line 3)', f'{link}, line 3', 'FAIL'),
            (f'({link}, line 2)', f'{link}, line 2', 'FAIL'),
            (f'{link} {link}, lines 2–3', f'{link}, line 3', 'BLOCKED'),
            (f'({link}, line 2)', '', 'BLOCKED'),
        ):
            with self.subTest(ttl_ref=ttl_ref, owner_ref=owner_ref):
                candidate = (
                    '# Service configuration\n\n'
                    f'- TTL: 60 s {ttl_ref}.\n'
                    f'- Owner: Team Blu {owner_ref}.\n\n'
                    'Other settings are out of scope.\n'
                ).encode()
                checks = oracle.document_checks(candidate)
                self.assertEqual('PASS', checks['ttl']['status'])
                self.assertEqual('PASS', checks['owner']['status'])
                self.assertEqual(expected, aggregate([(check, True) for check in checks.values()])['status'])

    def test_source_in_another_paragraph_does_not_inherit_a_bounded_range(self):
        document = (
            b'# Service configuration\n\n'
            b'Service settings from [spec](../documents/spec.md), line 1.\n\n'
            b'- TTL: 60 s.\n- Owner: Team Blu.\n\n'
            b'Other settings are out of scope.\n'
        )
        checks = oracle.document_checks(document)
        self.assertEqual('BLOCKED', checks['claim_verifiability']['status'])
        self.assertEqual({'PASS'}, {item['status'] for item in oracle.document_checks(
            document.replace(b', line 1', b'')).values()})

    def test_fact_local_links_do_not_hide_an_incomplete_first_summary(self):
        link = b'[spec](../documents/spec.md)'
        document = (
            b'# Service configuration\n\n'
            b'- TTL: 60 s (' + link + b', line 2).\n'
            b'- Owner: Team Blu (' + link + b', line 3).\n\n'
            b'Other settings are out of scope.\n\n'
        )
        for summary, expected in (
            (b', line 2.', 'FAIL'),
            (b', line 3.', 'FAIL'),
            (b', lines 2-3.', 'PASS'),
            (b', lines 2-3.\n\nSource: ' + link + b', line 3.', 'PASS'),
        ):
            with self.subTest(summary=summary):
                checks = oracle.document_checks(document + b'Source: ' + link + summary + b'\n')
                self.assertEqual(expected, checks['source_link']['status'])
                self.assertEqual(expected, aggregate([(check, True) for check in checks.values()])['status'])

    def test_limits_cannot_exclude_required_facts(self):
        for exclusion in (
            'TTL and ownership are out of scope.',
            'The scope excludes TTL.',
            'Owner is not covered.',
            'Only TTL is covered.',
            'La copertura non include la responsabilità.',
        ):
            with self.subTest(exclusion=exclusion):
                candidate = DOCUMENT + b'\n' + exclusion.encode() + b'\n'
                checks = oracle.document_checks(candidate)
                self.assertEqual('FAIL', checks['limits']['status'])

    def test_additional_claims_need_local_sources_and_review(self):
        extra = b'\n## Runtime\n\nThe service retries failed requests three times'
        for assertion, support in (
            (extra + b'.\n', 'FAIL'),
            (extra + b'.\nSource: [spec](../documents/spec.md).\n', 'FAIL'),
            (extra + b' ([spec](../documents/spec.md)).\n', 'PASS'),
            (b'\n[The service retries three times](../documents/spec.md).\n', 'PASS'),
        ):
            with self.subTest(assertion=assertion):
                checks = oracle.document_checks(DOCUMENT + assertion)
                self.assertEqual('PASS', checks['source_link']['status'])
                self.assertEqual(support, checks['claim_support']['status'])
                self.assertEqual('BLOCKED', checks['claim_verifiability']['status'])
                verdict = aggregate([(check, True) for check in checks.values()])
                self.assertEqual('FAIL' if support == 'FAIL' else 'BLOCKED', verdict['status'])

    def test_only_the_authorized_document_may_be_created(self):
        dirs = ['knowledge-base', 'documents']
        metadata = {'knowledge-base': {'attributes': 0, 'mtime': 1}}
        before = {'files': {oracle.SPEC: 'source'}, 'directories': dirs, 'metadata': metadata}
        after = {'files': {**before['files'], oracle.TARGET: 'new'}, 'directories': dirs,
                 'metadata': {'knowledge-base': {'attributes': 0, 'mtime': 2}}}
        self.assertTrue(oracle.authorized_delta(before, after))
        self.assertFalse(oracle.authorized_delta(before, {**after, 'files': {**after['files'], 'notes.md': 'new'}}))
        self.assertFalse(oracle.authorized_delta(before, {**after, 'files': {**after['files'], oracle.SPEC: 'changed'}}))
        self.assertFalse(oracle.authorized_delta({**before, 'files': after['files']}, after))

    def test_trace_write_and_readback_are_separate(self):
        for variant, wanted in (
            ('complete', ('PASS', 'PASS')),
            ('no-write', ('BLOCKED', 'BLOCKED')),
            ('no-read', ('PASS', 'BLOCKED')),
            ('read-first', ('PASS', 'BLOCKED')),
            ('truncated', ('BLOCKED', 'BLOCKED')),
            ('partial-output', ('PASS', 'BLOCKED')),
            ('wrong-output', ('PASS', 'FAIL')),
            ('transient-extra', ('FAIL', 'BLOCKED')),
        ):
            with self.subTest(variant=variant):
                raw = trace(variant)
                result = oracle.live_trace(PROCESS, raw, observe(raw, include_file_changes=True), ROOT, DOCUMENT)
                self.assertEqual(wanted, tuple(check['status'] for check in result))
        raw = trace()
        result = oracle.live_trace({**PROCESS, 'evidence_redacted': True}, raw,
                                   observe(raw, include_file_changes=True), ROOT, DOCUMENT)
        self.assertEqual(('BLOCKED', 'BLOCKED'), tuple(check['status'] for check in result))
