"""Campaign orchestration and reports; oracle decisions live in oracles.py."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import shutil
import uuid
from typing import Protocol

from .contract import parse, validate
from .oracles import REVISION, aggregate, evaluate
from .paths import (BoundaryError, FIXTURES, RESULTS, ROOT, contained, local,
                    read, sha, snapshot, tree_digest)

STATUSES = ('PASS', 'FAIL', 'BLOCKED', 'NOT_RUN', 'SKIP')


class OfflineRunner(Protocol):
    def run(self, workspace: Path) -> None:
        """Produce local artifacts, or raise; returning does not establish acceptance."""


class SnapshotRunner:
    def run(self, workspace):
        """H1 inspects copied fixture artifacts without invoking any external process."""


def decision(status, reason):
    return {'status': status, 'reason': reason}


def load(path):
    raw = read(Path(path))
    config = validate(parse(raw.decode('utf-8')))
    return config, raw


def reserve(results, campaign_id, run_id=None):
    results = contained(results, RESULTS)
    runs = contained(results / 'runs', RESULTS)
    runs.mkdir(parents=True, exist_ok=True)
    if run_id is None:
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:12]
    # Internal deterministic hook for collision tests; never accept a path as an ID.
    from .contract import identifier
    identifier('r-' + run_id.lower())
    directory = local(runs, campaign_id + '--' + run_id)
    directory.mkdir(exist_ok=False)
    return directory


def case(scenario, run, runner):
    result = {'id': scenario['id'], 'required': scenario['required'],
              'fixture': scenario['fixture'], 'fixture_sha256': None,
              'execution': 'not-started', 'checks': [], 'evidence': [],
              'dimensions': {}, 'overall': None}
    dimensions = result['dimensions']
    for name in ('preflight', 'completion', 'tool_use', 'evidence_integrity',
                 'semantic_accuracy', 'requirement_coverage', 'provenance'):
        dimensions[name] = decision('NOT_RUN', 'Evaluation has not run')
    dimensions['tool_use'] = decision('SKIP', 'H1 has no required external tool invocation')
    disposition = scenario.get('disposition', 'run')
    status = {'skip': 'SKIP', 'not_run': 'NOT_RUN'}.get(disposition)
    if status:
        for name in dimensions:
            dimensions[name] = decision(status, scenario['reason'])
        result['checks'] = [{**check, **decision(status, scenario['reason']), 'evidence': []}
                            for check in scenario['checks']]
        result['overall'] = decision(status, scenario['reason'])
        return result
    workspace = local(run, 'workspaces/' + scenario['id'])
    before = None
    try:
        source = local(FIXTURES, scenario['fixture'])
        before = snapshot(source)
        result['fixture_sha256'] = tree_digest(before)
        workspace.parent.mkdir(exist_ok=True)
        # Source tree has been checked for links/reparse points and bounded size.
        shutil.copytree(source, workspace, symlinks=True)
        if snapshot(workspace) != before:
            raise BoundaryError('Fixture copy differs from source')
        result['workspace'] = workspace.relative_to(run).as_posix()
        dimensions['preflight'] = decision('PASS', 'Bounded fixture copied into a fresh confined workspace')
        dimensions['provenance'] = decision('PASS', 'Configuration, fixture tree digest and oracle revision recorded')
        result['execution'] = 'running'
        runner.run(workspace)
        result['execution'] = 'completed'
        dimensions['completion'] = decision('PASS', 'Offline runner returned; oracle acceptance evaluated separately')
        for check in scenario['checks']:
            try:
                observed = evaluate(check, workspace, before)
            except Exception as error:
                # A defective oracle is an unavailable decision, never a PASS.
                observed = {**decision('BLOCKED', 'Oracle error: ' + type(error).__name__),
                            'id': check['id'], 'kind': check['kind'],
                            'required': check['required'], 'evidence': []}
            result['checks'].append(observed)
        dimensions['semantic_accuracy'] = aggregate([(c, c['required']) for c in result['checks']])
        after = snapshot(workspace)
        for path, digest in after['files'].items():
            result['evidence'].append({'id': 'file-' + str(len(result['evidence']) + 1),
                                      'path': (workspace / path).relative_to(run).as_posix(),
                                      'sha256': digest})
        # Verify preserved references, source immutability and tree boundaries independently.
        intact = snapshot(source) == before and all(
            sha(read(local(run, evidence['path']))) == evidence['sha256']
            for evidence in result['evidence'])
        for check in result['checks']:
            for evidence in check['evidence']:
                intact = intact and after['files'].get(evidence['path']) == evidence['sha256']
        result['observed_tree'] = after
        dimensions['evidence_integrity'] = decision('PASS' if intact else 'FAIL',
                                                  'Evidence digests and source immutability checked')
        coverage = all(c['status'] not in ('BLOCKED', 'NOT_RUN') for c in result['checks'] if c['required'])
        dimensions['requirement_coverage'] = decision('PASS' if coverage else 'NOT_RUN',
                                                    'Required decisions available' if coverage else 'Required decisions missing')
    except BoundaryError as error:
        dimensions['evidence_integrity'] = decision('FAIL', str(error))
        result['execution'] = 'failed'
    except Exception as error:
        name = 'preflight' if result['execution'] == 'not-started' else 'completion'
        dimensions[name] = decision('BLOCKED', 'Offline prerequisite or runner error: ' + type(error).__name__)
        result['execution'] = 'failed' if name == 'completion' else 'not-started'
    # Preserve bounded partial artifacts even when the runner raises before oracles run.
    if before is not None and dimensions['evidence_integrity']['status'] == 'NOT_RUN':
        try:
            after = snapshot(workspace)
            result['observed_tree'] = after
            result['evidence'] = [
                {'id': 'file-' + str(index), 'path': (workspace / path).relative_to(run).as_posix(),
                 'sha256': digest}
                for index, (path, digest) in enumerate(after['files'].items(), 1)]
            intact = snapshot(source) == before
            dimensions['evidence_integrity'] = decision('PASS' if intact else 'FAIL',
                                                      'Partial artifact digests and source immutability checked')
        except BoundaryError as error:
            dimensions['evidence_integrity'] = decision('FAIL', str(error))
        except OSError as error:
            dimensions['evidence_integrity'] = decision('BLOCKED', 'Partial evidence unavailable: ' + type(error).__name__)
    completed_ids = {c['id'] for c in result['checks']}
    for check in scenario['checks']:
        if check['id'] not in completed_ids:
            result['checks'].append({'id': check['id'], 'kind': check['kind'],
                                     'required': check['required'], 'evidence': [],
                                     **decision('NOT_RUN', 'Scenario did not reach oracle evaluation')})
    # No applicable required checks means excluded coverage, even if the runner returned.
    if not any(c['required'] for c in scenario['checks']) and all(
            d['status'] in ('PASS', 'SKIP') for d in dimensions.values()):
        result['overall'] = decision('SKIP', 'No applicable required checks')
    else:
        result['overall'] = aggregate([(value, True) for value in dimensions.values()])
    return result


def execute(campaign_path, results=RESULTS, *, runner=None, run_id=None):
    config, raw = load(campaign_path)
    run = reserve(Path(results), config['campaign_id'], run_id)
    # Exact validated input bytes are retained; no actual values become expected values.
    (run / 'campaign.json').write_bytes(raw)
    report = {'contract_version': 1, 'campaign_id': config['campaign_id'],
              'run_id': run.name, 'mode': 'offline', 'execution': 'running',
              'provenance': {'configuration_sha256': sha(raw),
                             'oracle_revision': REVISION,
                             'implementation_sha256': tree_digest(snapshot(ROOT / 'runner')),
                             'python': platform.python_version(), 'platform': platform.system(),
                             'product': 'Not consumed: synthetic fixture artifacts only'},
              'scenarios': []}
    for scenario in config['scenarios']:
        report['scenarios'].append(case(scenario, run, runner or SnapshotRunner()))
    report['execution'] = 'completed'
    report['overall'] = aggregate([(s['overall'], s['required']) for s in report['scenarios']])
    counts = Counter(s['overall']['status'] for s in report['scenarios'])
    report['counts'] = {status: counts[status] for status in STATUSES}
    (run / 'result.json').write_text(json.dumps(report, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')
    lines = ['# Offline campaign ' + config['campaign_id'], '',
             'Acceptance: ' + report['overall']['status'],
             'Technical execution: ' + report['execution'], '',
             '| Scenario | Execution | Acceptance | Reason |', '| --- | --- | --- | --- |']
    for scenario in report['scenarios']:
        reason = scenario['overall']['reason'].replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {scenario['id']} | {scenario['execution']} | {scenario['overall']['status']} | {reason} |")
    lines += ['', 'Counts: ' + ', '.join(f'{s}={report["counts"][s]}' for s in STATUSES), '',
              'See result.json for every oracle decision, observations, digests, errors and coverage gaps.',
              'campaign.json preserves exact input; workspaces/ preserves local evidence.',
              'No Codex, network, sandbox enforcement or live compatibility was evaluated.', '']
    (run / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    return run, report
