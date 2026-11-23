"""Check U01 against frozen facts and independent filesystem observations."""
import re

from .oracles import assessment
from .paths import sha

REVISION = 'p5c0-u01-offline-v1'
TARGET = 'knowledge-base/service.md'
SOURCE = 'documents/spec-v2.md'
GOLD_BASELINE = b'# Service\n\n## Current\nTTL: 60 s (v1).\n'
GOLD_SOURCE = (b'# Approved service specification v2\n\nTTL: 90 s.\n'
               b'This approved v2 supersedes v1 for TTL.\n')
GOLD_PROPOSAL = {'type': 'proposal', 'path': TARGET, 'old': '60 s', 'new': '90 s'}
DOCUMENT = re.compile(
    rb'# Service\n\n## Current\nTTL: (?P<current>[0-9]+ s) \(v2\)\.\n\n'
    rb'## History\nTTL: (?P<history>[0-9]+ s) \(v1, superseded by v2\)\.\n\n'
    rb'## Source\n\[Approved v2\]\(\.\./(?P<source>[a-z0-9./-]+)'
    rb'#L(?P<start>[1-9][0-9]{0,3})-L(?P<end>[1-9][0-9]{0,3})\)\n'
)


def verdict(ok, passed, failed):
    return assessment('PASS' if ok else 'FAIL', passed if ok else failed)


def observation(kind, path, content):
    return {'type': kind, 'path': path, 'sha256': sha(content)}


def workspace_delta(before, after):
    deleted = sorted(before['files'].keys() - after['files'].keys())
    added = sorted(after['files'].keys() - before['files'].keys())
    modified = sorted(
        name for name in before['files'].keys() & after['files'].keys()
        if before['files'][name] != after['files'][name]
    )
    metadata = sorted(
        name for name in before['metadata'].keys() & after['metadata'].keys()
        if before['metadata'][name] != after['metadata'][name]
    )
    allowed = (not added and not deleted and modified == [TARGET]
               and before['directories'] == after['directories']
               and set(metadata) <= {TARGET}
               and before['metadata'][TARGET]['attributes'] == after['metadata'][TARGET]['attributes'])
    return {
        **verdict(allowed, 'Only the existing service.md changed', 'Workspace delta differs from the write scope'),
        'added': added, 'removed': deleted, 'modified': modified, 'metadata_changed': metadata,
        'directories_added': sorted(set(after['directories']) - set(before['directories'])),
        'directories_removed': sorted(set(before['directories']) - set(after['directories'])),
    }


def document_checks(document, source, read_event):
    fields = DOCUMENT.fullmatch(document) if document is not None else None
    ttl = fields['current'].decode() if fields else None
    history = fields['history'].decode() if fields else None
    semantics = {
        **verdict(ttl == '90 s' and history == '60 s', '90 s current and 60 s historical only',
                  'Expected exactly 90 s current and 60 s historical only'),
        'current': ttl, 'history': history,
    }
    cited_path = fields['source'].decode() if fields else None
    cited = (int(fields['start']), int(fields['end'])) if fields else None
    lines = source.decode('utf-8').splitlines() if source == GOLD_SOURCE else []
    excerpt = lines[cited[0] - 1:cited[1]] if cited and cited[0] <= cited[1] <= len(lines) else []
    expected = read_event == observation('source-read', SOURCE, GOLD_SOURCE)
    supported = (fields is not None and cited_path == SOURCE and expected
                 and f'TTL: {ttl}.' in excerpt and 'This approved v2 supersedes v1 for TTL.' in excerpt)
    return semantics, {
        **verdict(supported, 'Observed approved v2 and verified cited TTL and supersession',
                  'The cited approved v2 lines do not support the applied TTL and supersession'),
        'observed_read': read_event, 'approved_sha256': sha(GOLD_SOURCE),
        'observed_sha256': sha(source) if source is not None else None,
        'cited_path': cited_path, 'cited_lines': cited, 'excerpt': excerpt,
    }


def readback_check(event, document):
    content = event.get('content') if event is not None else None
    matches = (document is not None and isinstance(content, str)
               and content.encode('utf-8', errors='surrogatepass') == document
               and event == {**observation('readback', TARGET, document), 'content': content})
    return {
        **verdict(matches, 'Readback bytes match the final file',
                  'Readback missing or different from the final file'),
        'observed_readback': event,
        'final_sha256': sha(document) if document is not None else None,
    }
