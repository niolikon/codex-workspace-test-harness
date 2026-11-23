"""Observable checks; runner completion and runner claims are not inputs."""
from .contract import parse
from .paths import BoundaryError, local, read, sha, snapshot

REVISION = 'h1-oracles-v1'


def assessment(status, reason):
    return {'status': status, 'reason': reason}


def aggregate(items):
    required = [item['status'] for item, needed in items if needed and item['status'] != 'SKIP']
    for status in ('FAIL', 'BLOCKED', 'NOT_RUN'):
        if status in required:
            return assessment(status, 'Required evaluations include ' + status)
    if required:
        return assessment('PASS', 'All applicable required evaluations passed')
    return assessment('SKIP', 'No applicable required evaluations')


def evaluate(check, workspace, before):
    evidence = []
    try:
        kind = check['kind']
        if kind in ('tree', 'unchanged'):
            actual = snapshot(workspace)
            matched = (sorted(actual['files']) == sorted(check['expected']) if kind == 'tree'
                       else actual == before)
            observation = actual
        else:
            path = local(workspace, check['path'])
            if kind == 'absent':
                matched = not path.exists()
                observation = {'exists': path.exists()}
            elif not path.is_file():
                matched, observation = False, {'file_exists': False}
            else:
                data = read(path)
                evidence.append({'path': check['path'], 'sha256': sha(data)})
                observation = {'file_exists': True, 'sha256': sha(data)}
                matched = (True if kind == 'exists' else
                           data.decode('utf-8') == check['expected'] if kind == 'text' else
                           # JSON equality must distinguish booleans from integers.
                           canonical(parse(data.decode('utf-8'))) == canonical(check['expected']))
        result = assessment('PASS' if matched else 'FAIL', 'Observed evidence matches expectation' if matched
                            else 'Observed evidence differs from independent expectation')
        result['observation'] = observation
    except BoundaryError as error:
        result = assessment('FAIL', str(error))
    except (UnicodeError, ValueError) as error:
        result = assessment('FAIL', 'Invalid evidence: ' + str(error))
    except OSError as error:
        result = assessment('BLOCKED', 'Evidence could not be read: ' + type(error).__name__)
    return {**result, 'id': check['id'], 'kind': check['kind'],
            'required': check['required'], 'evidence': evidence}


def canonical(value):
    import json
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
