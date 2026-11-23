"""Verify C02 fixture-specific equivalence and evidence from filesystem bytes."""
import re

from .paths import sha
from .knowledge_update_document import observation, verdict

REVISION = 'p5c0-c02-offline-v2'
TARGET = 'knowledge-base/a.md'
SENTINEL = 'knowledge-base/keep.md'
FIRST = ('documents/operations.md', 3, 3)
SECOND = ('documents/service-spec.md', 3, 4)
SOURCES = (FIRST[0], SECOND[0])
CLAIM = re.compile(r'- TTL: ([0-9]{1,5}) s\. Applicability: ([a-z]+)\. Origins: (.+)\.')
CITATION = re.compile(r'\[[^\[\]\n]+\]\(\.\./(documents/[a-z-]+\.md)#L([1-9][0-9]{0,3})'
                      r'(?:-L([1-9][0-9]{0,3}))?\)')
CITATION_SEPARATOR = re.compile(r'[ \t]*[,;][ \t]*')


def claims(document):
    if document is None:
        return []
    try:
        heading, blank, *body = document.decode('utf-8').splitlines()
    except (UnicodeError, ValueError):
        return []
    if heading != '# Cache TTL' or blank or not body:
        return []
    result = []
    for line in body:
        fields = CLAIM.fullmatch(line)
        if fields is None:
            return []
        ttl, applicability, references = fields.groups()
        origins = []
        position = 0
        while True:
            citation = CITATION.match(references, position)
            if citation is None:
                return []
            path, start, end = citation.groups()
            origins.append((path, int(start), int(end or start)))
            if citation.end() == len(references):
                break
            separator = CITATION_SEPARATOR.match(references, citation.end())
            if separator is None:
                return []
            position = separator.end()
        result.append((int(ttl), applicability, tuple(origins)))
    return result


def supports(origin, raw, ttl, applicability):
    path, start, end = origin
    lines = raw.decode('utf-8', errors='replace').splitlines() if raw is not None else []
    excerpt = lines[start - 1:end] if start <= end <= len(lines) else []
    expected = f'In {applicability}, the cache TTL is {ttl} s.'
    return {'path': path, 'lines': [start, end], 'excerpt': excerpt, 'supports_claim': expected in excerpt}


def gold_claims(distinct):
    return [(60, 'production', (FIRST,)), (60, 'staging' if distinct else 'production', (SECOND,))]


def document_checks(document, actual_sources, inputs, distinct, *, exact_ranges=True):
    expected = gold_claims(distinct)
    required_facts = sorted({(ttl, scope) for ttl, scope, _ in expected})
    actual = claims(document)
    facts = sorted((ttl, scope) for ttl, scope, _ in actual)
    semantics = verdict(facts == required_facts,
                        'Exactly one claim per original TTL and applicability',
                        'Claim count, TTL or applicability differs from the original equivalence classes')
    resolved = [supports(ref, actual_sources.get(ref[0]), ttl, scope)
                for ttl, scope, refs in actual for ref in refs]
    retained = sorted(ref for _, _, refs in actual for ref in refs)
    origins_match = (retained == sorted((FIRST, SECOND)) if exact_ranges
                     else sorted(ref[0] for ref in retained) == sorted(SOURCES))
    verified = (origins_match
                and all(source in actual_sources and actual_sources[source] == inputs[source] for source in SOURCES)
                and all(item['supports_claim'] for item in resolved))
    provenance = verdict(verified, 'Both original citations are intact and support their retained claim',
                         'An original citation is missing, repeated, altered or does not support its retained claim')
    return ({**semantics, 'expected_facts': required_facts, 'observed_claims': actual},
            {**provenance, 'expected_origins': [FIRST, SECOND], 'resolved_origins': resolved})


def workspace_delta(before, after):
    old, new = before['files'], after['files']
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    modified = sorted(name for name in old.keys() & new.keys() if old[name] != new[name])
    old_meta, new_meta = before['metadata'], after['metadata']
    metadata = sorted(name for name in old_meta.keys() & new_meta.keys() if old_meta[name] != new_meta[name])
    allowed = (not added and not removed and modified == [TARGET]
               and before['directories'] == after['directories'] and set(metadata) <= {TARGET}
               and old_meta[TARGET]['attributes'] == new_meta[TARGET]['attributes'])
    return {
        **verdict(allowed, 'Only the existing knowledge-base/a.md changed', 'Workspace delta exceeds the write scope'),
        'added': added, 'removed': removed, 'modified': modified, 'metadata_changed': metadata,
        'directories_added': sorted(set(after['directories']) - set(before['directories'])),
        'directories_removed': sorted(set(before['directories']) - set(after['directories'])),
    }


def readback_check(event, document):
    content = event.get('content') if event is not None else None
    matches = (document is not None and isinstance(content, str)
               and content.encode('utf-8', errors='surrogatepass') == document
               and event == {**observation('readback', TARGET, document), 'content': content})
    return {**verdict(matches, 'Readback bytes match the final file', 'Readback missing or different from the final file'),
            'event': event, 'final_sha256': sha(document) if document is not None else None}
