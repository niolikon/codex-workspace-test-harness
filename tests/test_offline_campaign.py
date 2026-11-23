"""H1 behavioral tests; all generated evidence stays in the external results root."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

from runner.campaign import execute, load
from runner.contract import parse, validate
from runner.oracles import aggregate
from runner.paths import BoundaryError, FIXTURES, RESULTS, ROOT, local, sha, snapshot, tree_digest


class OfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output = RESULTS / ('h1-tests-' + uuid.uuid4().hex)
        from runner.paths import contained
        contained(cls.output, RESULTS).mkdir(parents=True)

    def setUp(self):
        self.config, _ = load(FIXTURES / 'campaigns/pass.json')

    def run_config(self, config=None, **kwargs):
        path = self.output / ('input-' + uuid.uuid4().hex + '.json')
        path.write_text(json.dumps(self.config if config is None else config), encoding='utf-8')
        return execute(path, self.output, **kwargs)

    def test_valid_campaign(self):
        self.assertEqual(self.config, validate(self.config))

    def test_malformed_contracts(self):
        variants = []
        for key, value in [('contract_version', True), ('campaign_id', '../escape'),
                           ('scenarios', []), ('unexpected', True)]:
            bad = copy.deepcopy(self.config)
            bad[key] = value
            variants.append(bad)
        bad = copy.deepcopy(self.config)
        bad['scenarios'].append(copy.deepcopy(bad['scenarios'][0]))
        variants.append(bad)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate(value)
        for raw in ('{"a": 1, "a": 2}', '{"a": NaN}', '{"a": 1e999}', '{broken'):
            with self.assertRaises(ValueError):
                parse(raw)

    def test_unknown_or_ambiguous_oracles(self):
        for extra in ({'kind': 'claim-success'}, {'required': 'yes'}, {'expected': 'unneeded'}):
            bad = copy.deepcopy(self.config)
            bad['scenarios'][0]['checks'][0].update(extra)
            with self.assertRaises(ValueError):
                validate(bad)

    def test_missing_fixture_is_blocked(self):
        self.config['scenarios'][0]['fixture'] = 'workspaces/missing'
        _, report = self.run_config()
        self.assertEqual('BLOCKED', report['overall']['status'])
        self.assertTrue(all(c['status'] == 'NOT_RUN' for c in report['scenarios'][0]['checks']))

    def test_isolation_and_original_preserved(self):
        before = snapshot(FIXTURES)
        run, report = self.run_config()
        workspace = run / report['scenarios'][0]['workspace']
        self.assertNotEqual((FIXTURES / 'workspaces/basic').resolve(), workspace.resolve())
        (workspace / 'hello.txt').write_text('Only the disposable copy changes', encoding='utf-8')
        self.assertEqual(before, snapshot(FIXTURES))

    def test_pass_and_dimensions(self):
        _, report = self.run_config()
        scenario = report['scenarios'][0]
        self.assertEqual('PASS', report['overall']['status'])
        self.assertEqual('completed', scenario['execution'])
        self.assertEqual('SKIP', scenario['dimensions']['tool_use']['status'])
        self.assertTrue(all(c['status'] == 'PASS' for c in scenario['checks']))

    def test_real_discrepancy_is_fail(self):
        run, report = execute(FIXTURES / 'campaigns/fail.json', self.output)
        scenario = report['scenarios'][0]
        self.assertEqual('completed', scenario['execution'])
        self.assertEqual('PASS', scenario['dimensions']['completion']['status'])
        self.assertEqual('FAIL', report['overall']['status'])
        self.assertIn('FAIL', (run / 'report.md').read_text(encoding='utf-8'))

    def test_missing_required_evidence_is_fail(self):
        self.config['scenarios'][0]['checks'][0]['path'] = 'missing.txt'
        _, report = self.run_config()
        self.assertEqual('FAIL', report['overall']['status'])

    def test_runner_error_blocks_acceptance(self):
        class BrokenRunner:
            def run(self, workspace):
                raise RuntimeError('synthetic error')
        _, report = self.run_config(runner=BrokenRunner())
        self.assertEqual('BLOCKED', report['overall']['status'])
        self.assertEqual('failed', report['scenarios'][0]['execution'])
        self.assertTrue(report['scenarios'][0]['evidence'])

    def test_no_required_checks_is_skip(self):
        for check in self.config['scenarios'][0]['checks']:
            check['required'] = False
        _, report = self.run_config()
        self.assertEqual('SKIP', report['overall']['status'])

    def test_bounded_evidence(self):
        from runner.paths import read
        with patch('runner.paths.MAX_FILE', 1), self.assertRaises(BoundaryError):
            read(FIXTURES / 'workspaces/basic/hello.txt')
        with patch('runner.paths.MAX_FILES', 1), self.assertRaises(BoundaryError):
            snapshot(FIXTURES / 'workspaces/basic')

    def test_unreadable_tree_does_not_become_empty_success(self):
        def denied(*args, **kwargs):
            kwargs['onerror'](PermissionError('synthetic unreadable directory'))
        with patch('runner.paths.os.walk', side_effect=denied), self.assertRaises(PermissionError):
            snapshot(FIXTURES / 'workspaces/basic')

    def test_defective_oracle_is_not_pass(self):
        with patch('runner.campaign.evaluate', side_effect=RuntimeError('broken oracle')):
            _, report = self.run_config()
        self.assertEqual('BLOCKED', report['overall']['status'])
        self.assertEqual('completed', report['scenarios'][0]['execution'])
        self.assertTrue(all(c['status'] == 'BLOCKED' for c in report['scenarios'][0]['checks']))

    def test_not_run_and_skip(self):
        for disposition, status in [('not_run', 'NOT_RUN'), ('skip', 'SKIP')]:
            self.config['scenarios'][0].update(disposition=disposition, reason='Declared exclusion')
            _, report = self.run_config()
            self.assertEqual(status, report['overall']['status'])
            self.assertTrue(all(c['status'] == status for c in report['scenarios'][0]['checks']))

    def test_exclusion_requires_reason(self):
        self.config['scenarios'][0]['disposition'] = 'skip'
        with self.assertRaises(ValueError):
            validate(self.config)

    def test_traversal_and_windows_paths(self):
        for path in ('../escape', '/absolute', 'C:/escape', 'a/../../escape',
                     'a\\escape', 'a//b', 'a/./b', 'CON', 'aux.txt', 'file.'):
            with self.subTest(path=path):
                self.config['scenarios'][0]['fixture'] = path
                with self.assertRaises(ValueError):
                    validate(self.config)

    def test_check_path_traversal(self):
        self.config['scenarios'][0]['checks'][0]['path'] = '../outside.txt'
        with self.assertRaises(ValueError):
            validate(self.config)

    def test_results_outside_authorized_root_rejected(self):
        for root in (ROOT, ROOT.parent / 'codex-workspace-template', FIXTURES):
            with self.subTest(root=root), self.assertRaises(BoundaryError):
                execute(FIXTURES / 'campaigns/pass.json', root)

    def test_workspace_escape_rejected(self):
        with self.assertRaises(BoundaryError):
            local(self.output, '../escape')

    def test_symlink_escape_rejected(self):
        base = self.output / ('links-' + uuid.uuid4().hex)
        base.mkdir()
        try:
            (base / 'escape').symlink_to(ROOT, target_is_directory=True)
        except OSError as error:
            self.skipTest('Symlink creation unavailable: ' + type(error).__name__)
        with self.assertRaises(BoundaryError):
            snapshot(base)
        with self.assertRaises(BoundaryError):
            local(base, 'escape/README.md')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows junction behavior only')
    def test_windows_junction_escape_rejected(self):
        base = self.output / ('junction-' + uuid.uuid4().hex)
        base.mkdir()
        # Test setup creates a junction only in owned external evidence; never writes through it.
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(base / 'escape'), str(ROOT)],
                       check=True, capture_output=True)
        with self.assertRaises(BoundaryError):
            snapshot(base)
        with self.assertRaises(BoundaryError):
            local(base, 'escape/README.md')

    def test_collision_never_overwrites(self):
        run, _ = self.run_config(run_id='collision')
        before = (run / 'result.json').read_bytes()
        with self.assertRaises(FileExistsError):
            self.run_config(run_id='collision')
        self.assertEqual(before, (run / 'result.json').read_bytes())

    def test_provenance_and_evidence_digests(self):
        run, report = self.run_config()
        scenario = report['scenarios'][0]
        self.assertEqual(sha((run / 'campaign.json').read_bytes()), report['provenance']['configuration_sha256'])
        self.assertEqual(tree_digest(snapshot(FIXTURES / scenario['fixture'])), scenario['fixture_sha256'])
        self.assertEqual('h1-oracles-v1', report['provenance']['oracle_revision'])
        self.assertTrue(scenario['evidence'])
        for item in scenario['evidence']:
            self.assertEqual(item['sha256'], sha(local(run, item['path']).read_bytes()))
        self.assertEqual(report, json.loads((run / 'result.json').read_text(encoding='utf-8')))

    def test_aggregation_precedence_and_optional_visibility(self):
        def d(status):
            return {'status': status, 'reason': 'test'}
        for statuses, expected in [(['PASS'], 'PASS'), (['PASS', 'NOT_RUN'], 'NOT_RUN'),
                                   (['BLOCKED', 'NOT_RUN'], 'BLOCKED'),
                                   (['FAIL', 'BLOCKED'], 'FAIL'), (['SKIP'], 'SKIP')]:
            self.assertEqual(expected, aggregate([(d(s), True) for s in statuses])['status'])
        bad, _ = load(FIXTURES / 'campaigns/fail.json')
        bad['scenarios'][0]['required'] = False
        self.config['scenarios'] += bad['scenarios']
        _, report = self.run_config()
        self.assertEqual('PASS', report['overall']['status'])
        self.assertEqual(1, report['counts']['FAIL'])

    def test_unauthorized_changes_are_detected(self):
        class Mutator:
            def run(self, workspace):
                (workspace / 'unexpected.txt').write_text('changed', encoding='utf-8')
        _, report = self.run_config(runner=Mutator())
        self.assertEqual('FAIL', report['overall']['status'])
        observed = {c['id']: c['status'] for c in report['scenarios'][0]['checks']}
        self.assertEqual('FAIL', observed['preserved'])
        self.assertEqual('FAIL', observed['tree'])

    def test_json_false_is_not_zero(self):
        check = self.config['scenarios'][0]['checks'][2]
        check['expected']['synthetic'] = 1
        _, report = self.run_config()
        self.assertEqual('FAIL', report['overall']['status'])

    def test_cli_exit_codes_and_validation(self):
        for name, expected in [('pass', 0), ('fail', 1)]:
            command = [sys.executable, '-B', '-m', 'runner', '--campaign',
                       str(FIXTURES / f'campaigns/{name}.json'), '--results', str(self.output)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
            if name == 'pass':
                validation = subprocess.run(command + ['--validate-only'], cwd=ROOT,
                                            capture_output=True, text=True, timeout=30)
                self.assertEqual(0, validation.returncode)


if __name__ == '__main__':
    unittest.main()
