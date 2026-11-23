"""Independent I01 document checks and provisional Windows trace witnesses."""
from collections import defaultdict
import posixpath
import re

from .ask_oracle import READ_PREFIX, numbered_output
from .contract import parse
from .oracles import assessment
from .paths import sha

REVISION = 'p5c0-i01-oracle-v5'
SPEC = 'documents/spec.md'
TARGET = 'knowledge-base/service.md'
SOURCE = ('# Service specification', 'TTL: 60 s.', 'Owner: Team Blu.')
FACT_LINES = {'ttl': 2, 'owner': 3}
NUMBERED_READS = {
    READ_PREFIX + TARGET + "'",
    READ_PREFIX.replace('rg -n ', 'rg -n -H ') + TARGET + "'",
    f'rg -n -H "^" {TARGET}',
}
TTL_NAME = r'\b(?:ttl|time[ -]to[ -]live)\b'
OWNER_NAME = r'\b(?:owner(?:ship)?|responsabil[ei]|responsabilit[aà]|titolar[ei]|titolarit[aà]|proprietari[oa])\b'
FIELD_SEPARATOR = r'\s*(?:[:=|\-–—]|\bis\b|\bvale\b|\bè\b|\bdi\b)\s*'
TTL = re.compile(
    rf'(?:the |il )?{TTL_NAME}(?: del servizio|\s*\((?:{TTL_NAME})\))?'
    rf'(?:{FIELD_SEPARATOR})?(\d+(?:[.,]\d+)?)\s*([a-z]+)', re.I,
)
OWNER = re.compile(
    rf'(?:the |il )?(?:team )?{OWNER_NAME}(?:\s*\((?:{OWNER_NAME})\))?'
    rf'{FIELD_SEPARATOR}(.+)', re.I,
)
LINK = re.compile(r'(?<!!)\[([^\]\n]+)\]\(\s*<?([^<>\s)]+)>?(?:\s+"[^"\n]*")?\s*\)')
HEADING = re.compile(r'^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$')
LIST_ITEM = re.compile(r'^ {0,3}(?:[-+*]|\d+[.)])[ \t]+')
SERVICE = re.compile(r'\b(?:service|servizio)\b', re.I)
SCOPE_TERM = re.compile(
    r'\b(?:configuration|configurazione|baseline|settings|parametri|scope|ambito|perimetro|'
    r'coverage|copertura|responsabilità|ownership)\b', re.I,
)
SCOPE_STATEMENT = re.compile(
    r'(?:service (?:configuration|settings|baseline)|'
    r'(?:this (?:note|document) describes )?(?:the )?(?:configuration|settings) of the service|'
    r'(?:questa nota riguarda )?(?:i parametri|la configurazione) del servizio)', re.I,
)
LINE_REFERENCE = re.compile(r'\b(?:lines?|rig(?:a|he))\s+(\d+)(?:\s*[-–—]\s*(\d+))?', re.I)
SOURCE_LABEL = re.compile(
    r'(?:(?:source|fonte)\s*:|i dati provengono dalla|(?:approved )?specification\s*:)?'
    rf'\s*(?:,?\s*{LINE_REFERENCE.pattern})?', re.I,
)
SOURCE_TITLE = re.compile(
    r'(?:(?:approved |service )?spec(?:ification)?|specifica(?: approvata| del servizio)?)'
    rf'(?:\s*,?\s*{LINE_REFERENCE.pattern})?', re.I,
)
TRAILING_SOURCE = re.compile(
    rf'\s*(?:\(\x00(?:\s*,?\s*{LINE_REFERENCE.pattern})?\s*\)'
    rf'|\x00(?:\s*,?\s*{LINE_REFERENCE.pattern})?)\s*$', re.I,
)
ONLY_COVERED = re.compile(
    r'only (.+?) (?:is|are) covered|la copertura si limita (?:al|a|alla) (.+)', re.I,
)
EXCLUDED = re.compile(
    r'(.+?) (?:(?:is|are) (?:out of scope|not (?:covered|included|documented)|excluded)|'
    r'(?:è|sono) (?:fuori (?:ambito|scope|perimetro)|esclus[oaie]|non copert[oaie])|'
    r'non (?:è|sono) (?:copert[oaie]|inclus[oaie]|documentat[oaie]))|'
    r'(?:the (?:scope|coverage) (?:excludes|does not cover)|'
    r'(?:l[’\']ambito|la copertura) (?:esclude|non include|non copre)) (.+)', re.I,
)
KNOWN_LIMIT = re.compile(
    r'la specifica non indica a cosa si applichi il ttl|'
    r'il comportamento a runtime resta da verificare|'
    r'gli altri dettagli del servizio non sono documentati|'
    r'other (?:service )?(?:settings|details) are out of scope', re.I,
)
KNOWN_TITLES = re.compile(
    r'(?:(?:service|servizio)(?: (?:configuration|settings|baseline|configurazione))?|'
    r'operational notes|scheda operativa|configurazione(?: e responsabilità)?|configuration|settings|'
    r'scope|ambito|observed facts|sources?|fonti|limits|limiti(?: delle informazioni)?|runtime)', re.I,
)
FACT_TOPICS = re.compile(
    rf'(?:the |il |la )?(?:{TTL_NAME}|{OWNER_NAME})'
    rf'\s+(?:and|e)\s+(?:the |il |la |al |alla )?(?:{TTL_NAME}|{OWNER_NAME})', re.I,
)


def source_matches(source):
    return tuple(source.decode('utf-8-sig').splitlines()) == SOURCE


def document_blocks(prose):
    """Yield line, active heading ancestry, heading flag and text."""
    headings, paragraph = [], []
    start = 0
    for number, line in enumerate([*prose.splitlines(), ''], 1):
        heading = HEADING.fullmatch(line)
        item = LIST_ITEM.match(line)
        if paragraph and (heading or item or not line.strip()):
            yield start, tuple(headings), False, ' '.join(paragraph)
            paragraph.clear()
        if heading:
            level = len(heading[1])
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, heading[2]))
            yield number, tuple(headings), True, heading[2]
        elif line.strip():
            if not paragraph:
                start = number
            paragraph.append(line[item.end():].strip() if item else line.strip())


def valid_source(destination):
    path, separator, fragment = destination.partition('#')
    return (posixpath.normpath(posixpath.join('knowledge-base', path)) == SPEC
            and (not separator or fragment == 'service-specification'))


def cited_facts(line_references):
    """Return the I01 facts on cited lines of the frozen source, or None for invalid ranges."""
    if not line_references:
        return set(FACT_LINES)
    supported = set()
    for first, last in line_references:
        try:
            start, end = int(first), int(last or first)
        except ValueError:
            return None
        if not 1 <= start <= end <= len(SOURCE):
            return None
        supported.update(fact for fact, line in FACT_LINES.items() if start <= line <= end)
    return supported


def heading_scope(headings):
    title = ' '.join(text for _, text in headings)
    return bool(SERVICE.search(title) and SCOPE_TERM.search(title))


def document_checks(data):
    try:
        markdown = data.decode('utf-8-sig')
    except UnicodeError:
        return {'encoding': assessment('FAIL', 'Target is not UTF-8 Markdown')}
    if '\x00' in markdown:
        return {'encoding': assessment('FAIL', 'Target Markdown contains a NUL character')}
    # Exclude examples and comments, then flatten common inline formatting.
    prose = re.sub(r'(?ms)^ {0,3}(\x60{3,}|~{3,})[^\n]*\n.*?(?:^ {0,3}\1[ \t]*$|\Z)', '', markdown)
    prose = re.sub(r'(?s)<!--.*?(?:-->|\Z)', '', prose)
    prose = re.sub(r'~~[^~\n]+~~', '', prose)
    prose = '\n'.join(line for line in prose.splitlines()
                      if not line.startswith(('    ', '\t')) and not line.lstrip().startswith('>'))
    links = [destination for _, destination in LINK.findall(prose)]
    sourced = bool(links) and all(map(valid_source, links))
    ttl, owners, fact_scopes = [], [], []
    invalid_line_references = []
    unsupported_fact_references = set()
    unsupported, unverified = [], []
    observed_facts, pending_facts = set(), {}
    has_bounded_source = has_whole_source = has_summary_source = False
    scope = limits = excludes_required = False
    for number, headings, heading, block in document_blocks(prose):
        # Keep punctuation in link destinations out of sentence boundaries.
        references = LINK.findall(block)
        marked = LINK.sub('\x00', block).translate(str.maketrans('', '', '*_\x60'))
        sentences = [marked] if heading else re.split(
            rf'[.!?;](?=\s|$)|\s+(?:and|e)\s+(?=(?:the |il )?{TTL_NAME}{FIELD_SEPARATOR})',
            marked, flags=re.I,
        )
        block_source = None
        for sentence in sentences:
            count = sentence.count('\x00')
            links, references = references[:count], references[count:]
            cited = bool(links) and all(valid_source(destination) for _, destination in links)
            line_references, coverage = [], None
            if cited:
                line_references = LINE_REFERENCE.findall(sentence)
                for label, _ in links:
                    line_references.extend(LINE_REFERENCE.findall(label))
                coverage = cited_facts(line_references)
                if coverage is None:
                    invalid_line_references.append(number)
                    cited = False
                elif line_references and (len(links) != 1 or len(line_references) != 1):
                    unverified.append(number)
                    coverage = None
            source_titles = bool(links) and all(SOURCE_TITLE.fullmatch(label) for label, _ in links)
            plain = sentence.replace('(\x00)', '').replace('\x00', '').strip()
            if not sentence.strip():
                continue
            if source_titles:
                if SOURCE_LABEL.fullmatch(plain):
                    if coverage is not None:
                        has_bounded_source |= bool(line_references)
                        has_whole_source |= not line_references
                        # A first summary cites all preceding facts unless a whole-file
                        # source has already been established in the document.
                        expected_facts = (pending_facts if has_summary_source or has_whole_source
                                          else observed_facts)
                        if expected_facts:
                            if coverage.issuperset(expected_facts):
                                has_summary_source = True
                            else:
                                unsupported_fact_references.add(number)
                        elif line_references:
                            # A supplementary citation must support a fact already stated.
                            if not observed_facts:
                                unverified.append(number)
                            elif not observed_facts & coverage:
                                unsupported_fact_references.add(number)
                        pending_facts.clear()
                    continue
                plain = TRAILING_SOURCE.sub('', sentence).replace('\x00', '').strip()
                plain = re.sub(r' from(?: the)?$', '', plain, flags=re.I)
            plain = plain.strip('|').strip()
            ttl_match, owner_match = TTL.fullmatch(plain), OWNER.fullmatch(plain)
            only, excluded = ONLY_COVERED.fullmatch(plain), EXCLUDED.fullmatch(plain)
            if links and not source_titles:
                (unverified if cited else unsupported).append(number)
            if SCOPE_STATEMENT.fullmatch(plain):
                scope = True
                if source_titles:
                    block_source = coverage
                    if coverage is not None:
                        has_bounded_source |= bool(line_references)
                        has_whole_source |= not line_references
            elif only or excluded:
                subject = next(group for group in (only or excluded).groups() if group is not None)
                names_required = [bool(re.search(name, subject, re.I)) for name in (TTL_NAME, OWNER_NAME)]
                excludes_required |= not all(names_required) if only else any(names_required)
                known_limit = bool(KNOWN_LIMIT.fullmatch(plain))
                limits |= bool(only) or known_limit
                if ((only and all(names_required) and not FACT_TOPICS.fullmatch(subject))
                        or (excluded and not any(names_required) and not known_limit)):
                    unverified.append(number)
            elif KNOWN_LIMIT.fullmatch(plain):
                limits = True
            elif ttl_match or owner_match:
                if ttl_match:
                    ttl.append(ttl_match.groups())
                    fact = 'ttl'
                else:
                    owners.append(owner_match[1].strip())
                    fact = 'owner'
                fact_scopes.append(heading_scope(headings))
                observed_facts.add(fact)
                fact_coverage = coverage if links else block_source
                if links and coverage is not None:
                    has_bounded_source |= bool(line_references)
                if fact_coverage is None:
                    pending_facts[fact] = number
                elif fact not in fact_coverage:
                    unsupported_fact_references.add(number)
            elif heading and KNOWN_TITLES.fullmatch(plain):
                continue
            elif cited or heading:
                unverified.append(number)
            else:
                unsupported.append(number)
    if has_bounded_source and not has_whole_source:
        unverified.extend(pending_facts.values())
    verdicts = {
        'ttl': bool(ttl) and all(
            value.replace(',', '.') in ('60', '60.0')
            and unit.casefold() in ('s', 'second', 'seconds', 'secondo', 'secondi')
            for value, unit in ttl),
        'owner': bool(owners) and all(owner.casefold() == 'team blu' for owner in owners),
        'scope': scope or (bool(fact_scopes) and all(fact_scopes)),
        'limits': limits and not excludes_required,
        'source_link': sourced and not invalid_line_references and not unsupported_fact_references,
    }
    source_issues = []
    if invalid_line_references:
        source_issues.append(f'Invalid source line references in blocks starting at lines '
                             f'{sorted(set(invalid_line_references))}')
    if unsupported_fact_references:
        source_issues.append(f'Cited source lines do not support the associated facts in blocks '
                             f'starting at lines {sorted(unsupported_fact_references)}')
    checks = {name: assessment('PASS' if valid else 'FAIL', reason)
              for (name, valid), reason in zip(verdicts.items(), (
                  'Asserted TTL compared with the frozen 60-second fact',
                  'Asserted owner compared with the frozen Team Blu fact',
                  'Checked the service scope in prose and in the claims’ heading ancestry',
                  'Coverage excludes at least one required fact' if excludes_required else
                  'Checked for a coverage limit consistent with the required facts',
                  '. '.join(source_issues) if source_issues else
                  'Checked Markdown link destinations, line references and associated facts against the frozen source',
              ))}
    checks['claim_support'] = assessment(
        'FAIL' if unsupported else 'PASS',
        f'Additional text without a claim-local source in blocks starting at lines {unsupported}'
        if unsupported else 'No additional claim without a source was detected',
    )
    checks['claim_verifiability'] = assessment(
        'BLOCKED' if unverified or unsupported else 'PASS',
        f'Outside the finite I01 oracle in blocks starting at lines {sorted(set(unverified + unsupported))}'
        if unverified or unsupported else 'All assertions fit the finite I01 oracle',
    )
    return checks


def usable(process, raw):
    return (process['launched'] and process['job_drained'] and not process['stop_reason']
            and not process['evidence_redacted'] and not process['stream_limit_reached']
            and raw.endswith(b'\n'))


def simulated_trace(process, raw, source, target):
    blocked = assessment('BLOCKED', 'Simulated trace is incomplete or unavailable')
    if not usable(process, raw):
        return blocked, blocked, blocked
    try:
        events = [parse(line.decode('utf-8')) for line in raw.splitlines()]
    except (ValueError, RecursionError):
        return blocked, blocked, blocked
    if events[-1:] != [{'type': 'turn.completed'}]:
        return blocked, blocked, blocked
    completion = assessment('PASS', 'Simulator reached its terminal marker')
    digest = sha(target) if target is not None else None
    expected = [
        {'type': 'harness.simulated_read', 'path': SPEC, 'sha256': sha(source)},
        {'type': 'harness.simulated_write', 'path': TARGET, 'sha256': digest},
        {'type': 'harness.simulated_readback', 'path': TARGET, 'sha256': digest},
        {'type': 'turn.completed'},
    ]
    wrote = digest is not None and events[:2] == expected[:2]
    write = assessment('PASS', 'Simulated creation matches the post-run file') if wrote else blocked
    if wrote and events == expected:
        readback = assessment('PASS', 'Simulated read followed creation and matches the post-run file')
    elif wrote and events[2:-1] == [{'type': 'harness.simulated_readback_skipped'}]:
        readback = assessment('FAIL', 'Simulator explicitly skipped the required readback')
    else:
        readback = blocked
    return completion, write, readback


def read_rows(output, path=TARGET):
    if not isinstance(output, str) or not output.endswith('\n'):
        return []
    numbered = numbered_output(output, {path}).get(path)
    if numbered:
        return numbered
    # Single-file rg omits the path prefix unless -H is present.
    rows = output.replace('\r\n', '\n')
    parts = [row.partition(':') for row in rows[:-1].split('\n')]
    if any(not separator or not number.isascii() or not number.isdecimal()
           for number, separator, _ in parts):
        return []
    return [(number, text) for number, _, text in parts]


def live_trace(process, raw, events, workspace, target):
    """Accept only paired, ordered events and an exact read of the final text."""
    write = assessment('BLOCKED', 'No conclusive creation event for the authorized target')
    readback = assessment('BLOCKED', 'No conclusive full read after observed creation')
    starts, terminal = events['turn_started'], events['terminal']
    if (not usable(process, raw) or events['malformed'] or events['omitted_lines']
            or len(starts) != 1 or len(terminal) != 1 or starts[0] >= terminal[0]['line']):
        blocked = assessment('BLOCKED', 'Live trace is incomplete, ambiguous or redacted')
        return blocked, blocked

    frames = defaultdict(list)
    for line, kind, item in [*events['commands'], *events['file_changes']]:
        if isinstance(item.get('id'), str) and item['id']:
            frames[item['id']].append((line, kind, item))
    creation_line = 0
    reads = []
    valid_paths = {TARGET, str(workspace / TARGET).replace('\\', '/')}
    for pair in frames.values():
        if len(pair) != 2:
            continue
        (start, start_kind, started), (end, end_kind, done) = pair
        if (not starts[0] < start < end < terminal[0]['line']
                or (start_kind, end_kind) != ('item.started', 'item.completed')
                or started.get('status') != 'in_progress' or done.get('status') != 'completed'
                or started['type'] != done['type']):
            continue
        witness = {'start_line': start, 'completion_line': end, 'item_id': done['id']}
        if done['type'] == 'file_change':
            changes = done.get('changes')
            if not isinstance(changes, list) or not changes or changes != started.get('changes'):
                continue
            if any(isinstance(change, dict) and isinstance(change.get('path'), str)
                   and change.get('kind') in ('add', 'update', 'delete')
                   and (change['path'].replace('\\', '/') not in valid_paths or change['kind'] != 'add')
                   for change in changes):
                return ({**assessment('FAIL', 'Trace shows a change outside the creation authorization'),
                         **witness}, readback)
            change = changes[0]
            if (len(changes) == 1 and isinstance(change, dict) and change.get('kind') == 'add'
                    and isinstance(change.get('path'), str) and change['path'].replace('\\', '/') in valid_paths
                    and target is not None and end > creation_line):
                creation_line = end
                write = {**assessment('PASS', 'Paired creation event matches the observed new target'),
                         **witness}
        elif (isinstance(done.get('command'), str) and done['command'] in NUMBERED_READS
              and done['command'] == started.get('command') and type(done.get('exit_code')) is int
              and done['exit_code'] == 0):
            reads.append((start, done.get('aggregated_output'), witness))

    if not creation_line or target is None:
        return write, readback
    try:
        lines = target.decode('utf-8').replace('\r\n', '\n').removesuffix('\n').split('\n')
    except UnicodeError:
        return write, readback
    expected = [(str(number), text) for number, text in enumerate(lines, 1)]
    for start, output, witness in sorted(reads, key=lambda read: read[0], reverse=True):
        if start <= creation_line:
            continue
        rows = read_rows(output)
        if rows == expected:
            return write, {**assessment('PASS', 'Numbered read after creation matches the full final text'),
                           **witness, 'target_sha256': sha(target)}
        if rows and any((number, text) not in expected for number, text in rows):
            return write, {**assessment('FAIL', 'Numbered read contradicts the post-run target'),
                           **witness, 'target_sha256': sha(target)}
    return write, readback


def authorized_delta(before, after):
    if TARGET in before['files'] or TARGET not in after['files']:
        return False
    if before['directories'] != after['directories']:
        return False
    if before['files'] != {name: digest for name, digest in after['files'].items()
                            if name != TARGET}:
        return False
    # Creating the file can change its existing parent directory's mtime.
    for name, metadata in before['metadata'].items():
        observed = after['metadata'].get(name)
        if name == 'knowledge-base':
            if observed is None or observed['attributes'] != metadata['attributes']:
                return False
        elif observed != metadata:
            return False
    return True
