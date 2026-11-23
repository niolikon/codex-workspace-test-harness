
"""A01 gold facts, frozen source references and conservative trace observations."""
from collections import defaultdict

from .contract import parse
from .oracles import aggregate, assessment
from .paths import sha

REVISION = 'p5c0-a01-oracle-v2'
SPEC = 'documents/spec.md'
CODE = 'documents/service.py'
READ_PATHS = ('.agents/skills/ask/SKILL.md', '.agents/skills/_contracts/ask.md',
              '.agents/skills/_contracts/authorization.md', SPEC, CODE)
READ_ARGUMENTS = {*READ_PATHS, 'AGENTS.md', '.codex-workspace/manifest.json'}
# Serialized command shape observed in the pinned Windows trace.
READ_PREFIX = r'''"C:\\Windows\\System32\\cmd.exe" /c 'rg -n "''^" '''
GOLD = {
    'ttl_seconds': (60, {(SPEC, 2): 'TTL: 60 s', (CODE, 1): 'TTL_SECONDS = 60'}),
    'owner': ('Team Blu', {(SPEC, 3): 'Responsabile: Team Blu'}),
}
DIMENSIONS = ('preflight', 'completion', 'tool_use', 'evidence_integrity',
              'semantic_accuracy', 'requirement_coverage', 'provenance')
CHECKS = ('ttl_seconds', 'owner', 'citations', 'workspace_unchanged')
USAGE_FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens')
MAX_OBSERVED_LINES = 4096


def observe(raw, *, simulated=False, include_file_changes=False):
    """Identify known frames without guessing from command strings or unknown events."""
    lines = raw.removesuffix(b'\n').split(b'\n') if raw else []
    observation = {'turn_started': [], 'terminal': [], 'messages': [], 'tool_events': [], 'commands': [],
                   'simulated_reads': [], 'unknown': [], 'malformed': [],
                   'omitted_lines': max(0, len(lines) - MAX_OBSERVED_LINES)}
    if include_file_changes:
        observation['file_changes'] = []
    for number, line in enumerate(lines[:MAX_OBSERVED_LINES], 1):
        try:
            if not line or len(line) > 128 * 1024:
                raise ValueError
            event = parse(line.decode('utf-8'))
            if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                raise ValueError
            kind = event['type']
            if kind == 'turn.started':
                observation['turn_started'].append(number)
            elif kind == 'turn.completed':
                usage = event.get('usage')
                if not isinstance(usage, dict) or not all(
                        type(usage.get(key)) is int and 0 <= usage[key] < 2**53 for key in USAGE_FIELDS):
                    raise ValueError
                observation['terminal'].append({'line': number, 'type': kind,
                                                'usage': {key: usage[key] for key in USAGE_FIELDS}})
            elif kind in ('turn.failed', 'error'):
                observation['terminal'].append({'line': number, 'type': kind})
            elif kind == 'thread.started' and isinstance(event.get('thread_id'), str):
                continue
            elif (kind in ('item.started', 'item.completed') and isinstance(event.get('item'), dict)
                  and event['item'].get('type') == 'command_execution'):
                observation['commands'].append((number, kind, event['item']))
                if kind == 'item.completed':
                    observation['tool_events'].append({'line': number, 'type': 'command_execution'})
            elif (include_file_changes and kind in ('item.started', 'item.completed')
                  and isinstance(event.get('item'), dict)
                  and event['item'].get('type') == 'file_change'):
                observation['file_changes'].append((number, kind, event['item']))
            elif kind == 'item.completed' and isinstance(event.get('item'), dict):
                item = event['item']
                if (item.get('type') == 'agent_message' and isinstance(item.get('text'), str)
                        and isinstance(item.get('id'), str)):
                    observation['messages'].append({'line': number, 'text': item['text']})
                elif item.get('type') in ('mcp_tool_call', 'dynamic_tool_call'):
                    observation['tool_events'].append({'line': number, 'type': item['type']})
                else:
                    observation['unknown'].append(number)
            elif kind == 'harness.simulated_read' and simulated:
                path, digest = event.get('path'), event.get('sha256')
                if not isinstance(path, str) or len(path) > 256 or not isinstance(digest, str) or len(digest) != 64:
                    raise ValueError
                observation['simulated_reads'].append({
                    'line': number, 'path': path, 'sha256': digest})
            else:
                observation['unknown'].append(number)
        except (UnicodeError, ValueError, RecursionError):
            observation['malformed'].append(number)
    return observation


def numbered_output(output, paths):
    """Return each file's ordered rg -n rows, including blank source lines."""
    rows = defaultdict(list)
    if not isinstance(output, str) or not output.endswith('\n'):
        return rows
    for row in output.replace('\r\n', '\n')[:-1].split('\n'):
        path, _, tail = row.partition(':')
        number, separator, text = tail.partition(':')
        if path not in paths or not separator or not number.isascii() or not number.isdecimal():
            return {}
        rows[path].append((number, text))
    return rows


def verify_reads(process, events, raw, frozen):
    """Correlate completed command pairs with full numbered output and frozen bytes."""
    files = {path: assessment('BLOCKED', 'No matching successful command and output')
             for path in READ_PATHS}
    starts, terminal = events['turn_started'], events['terminal']
    if (not process['launched'] or not process['job_drained'] or process['stop_reason']
            or process['evidence_redacted'] or process['stream_limit_reached']
            or events['malformed'] or events['omitted_lines'] or not raw.endswith(b'\n')
            or len(starts) != 1 or len(terminal) != 1 or starts[0] >= terminal[0]['line']):
        return {**assessment('BLOCKED', 'Trace is incomplete, ambiguous or redacted'), 'files': files}
    commands = defaultdict(list)
    for line, kind, item in events['commands']:
        if isinstance(item.get('id'), str) and item['id']:
            commands[item['id']].append((line, kind, item))

    for item_id, frames in commands.items():
        if len(frames) != 2:
            continue
        (start, start_kind, started), (end, end_kind, completed) = frames
        command = completed.get('command')
        if (not starts[0] < start < end < terminal[0]['line']
                or (start_kind, end_kind) != ('item.started', 'item.completed')
                or started.get('status') != 'in_progress' or started.get('command') != command
                or not isinstance(command, str) or not command.startswith(READ_PREFIX)
                or not command.endswith("'")):
            continue
        arguments = command[len(READ_PREFIX):-1].split(' ')
        paths = set(arguments)
        # The path prefix in rg output is reliable for this multi-file invocation.
        if len(paths) < 2 or len(paths) != len(arguments) or not paths <= READ_ARGUMENTS:
            continue
        exit_code = completed.get('exit_code')
        rows = numbered_output(completed.get('aggregated_output'), paths)
        for path in paths.intersection(files):
            if files[path]['status'] == 'PASS' or path not in frozen:
                continue
            witness = {'start_line': start, 'completion_line': end, 'item_id': item_id}
            if type(exit_code) is int and exit_code != 0:
                files[path] = {**assessment('FAIL', 'Read command exited nonzero'), **witness,
                               'exit_code': exit_code}
                continue
            if type(exit_code) is not int or completed.get('status') != 'completed' or path not in rows:
                continue
            text = frozen[path].decode('utf-8').replace('\r\n', '\n')
            expected = [(str(number), line)
                        for number, line in enumerate(text.removesuffix('\n').split('\n'), 1)]
            matched = rows[path] == expected
            files[path] = {
                **assessment('PASS' if matched else 'FAIL',
                             'Full numbered output matches frozen file' if matched
                             else 'Numbered output differs from frozen file'),
                **witness, 'exit_code': exit_code, 'frozen_sha256': sha(frozen[path]),
            }
    return {**aggregate([(result, True) for result in files.values()]), 'files': files}


def completion(process, events, raw):
    stop = process['stop_reason']
    if stop in ('wall-timeout', 'idle-timeout', 'stream-limit'):
        return assessment('FAIL', 'Invocation exceeded ' + stop)
    if stop or not process['job_drained']:
        return assessment('BLOCKED', 'Supervisor or capture failed')
    if process['exit_code'] != 0 or any(
            e['type'] in ('turn.failed', 'error') for e in events['terminal']):
        return assessment('FAIL', 'Process or turn reported failure')
    if (events['malformed'] or events['omitted_lines'] or not raw.endswith(b'\n') or process['evidence_redacted']
            or process['stream_limit_reached']):
        return assessment('BLOCKED', 'Trace is malformed, incomplete or redacted')
    if not events['terminal']:
        return assessment('BLOCKED', 'No recognized terminal event')
    if (len(events['turn_started']) != 1 or len(events['terminal']) != 1
            or not events['messages']
            or not events['turn_started'][0] < events['messages'][-1]['line'] < events['terminal'][0]['line']):
        return assessment('BLOCKED', 'No unambiguous final message in a single completed turn')
    return assessment('PASS', 'Completed turn has a final message and the process tree exited')


def answer_checks(text, sources):
    try:
        answer = parse(text)
        if not isinstance(answer, dict) or set(answer) != {*GOLD, 'citations'}:
            raise ValueError
    except (ValueError, RecursionError):
        return {name: assessment('FAIL', 'Final message does not match the A01 answer format')
                for name in ('ttl_seconds', 'owner', 'citations')}
    checks = {
        name: assessment('PASS' if type(answer[name]) is type(value) and answer[name] == value else 'FAIL',
                         f'{name} compared with independently fixed gold')
        for name, (value, _) in GOLD.items()
    }
    citations = answer['citations']
    supported = isinstance(citations, dict) and set(citations) == set(GOLD)
    if supported:
        for name, (_, anchors) in GOLD.items():
            entries = citations[name]
            if not isinstance(entries, list) or not entries:
                supported = False
                break
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {'path', 'line'}:
                    supported = False
                    break
                path, number = entry['path'], entry['line']
                if not isinstance(path, str) or type(number) is not int:
                    supported = False
                    break
                anchor = anchors.get((path, number))
                lines = sources.get(path, '').splitlines()
                if (not anchor or not 1 <= number <= len(lines) or lines[number - 1] != anchor
                        or checks[name]['status'] != 'PASS'):
                    supported = False
                    break
    checks['citations'] = assessment('PASS' if supported else 'FAIL',
                                     'Cited paths and lines compared with frozen claim-specific source bytes')
    return checks


def tool_use(observations, frozen_tree):
    reads = observations['simulated_reads']
    if not reads:
        return assessment('BLOCKED', 'No simulated source reads observed')
    final_line = observations['messages'][-1]['line'] if observations['messages'] else float('inf')
    if (len(reads) == 2 and all(isinstance(item['path'], str) and
            frozen_tree['files'].get(item['path']) == item['sha256'] for item in reads)
            and {item['path'] for item in reads} == {SPEC, CODE}
            and observations['turn_started']
            and all(observations['turn_started'][0] < item['line'] < final_line
                    for item in reads)):
        return assessment('PASS', 'Simulator read both sources with matching digests before answering')
    return assessment('FAIL', 'Simulated source-read evidence differs from the frozen inputs')


def coverage(checks):
    if all(check['status'] in ('PASS', 'FAIL') for check in checks.values()):
        return assessment('PASS', 'All four A01 acceptance checks reached a verdict')
    return assessment('NOT_RUN', 'One or more A01 acceptance checks could not be evaluated')


def semantics(checks):
    return aggregate([(checks[name], True) for name in ('ttl_seconds', 'owner', 'citations')])
