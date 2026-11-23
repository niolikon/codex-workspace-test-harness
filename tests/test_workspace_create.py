"""Native workspace creation and Windows process supervision checks."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
import unittest
import uuid

from runner.paths import RESULTS, no_links
from runner.windows_process import controlled_env, run_process

PRODUCT = Path(os.environ['CODEX_PRODUCT_ROOT']) if os.environ.get('CODEX_PRODUCT_ROOT') else None
BASH = Path(os.environ.get('CODEX_GIT_BASH', 'C:/devtools/Git/bin/bash.exe'))
PS = Path('C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe')
ROOT = Path(__file__).resolve().parents[1]
INVOCATION_WALL = 45
INVOCATION_IDLE = 15
INVOCATION_STREAM = 64 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def snapshot(root):
    """Include directory topology and exact file bytes, with no semantic normalization."""
    return {p.relative_to(root).as_posix(): None if p.is_dir() else p.read_bytes()
            for p in root.rglob('*')}


@unittest.skipUnless(PRODUCT is not None, 'NOT_RUN: CODEX_PRODUCT_ROOT must select Product')
class NewProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != 'nt' or not PS.is_file() or not BASH.is_file():
            raise RuntimeError('BLOCKED: native Windows PowerShell and Git Bash required')
        no_links(PRODUCT)
        no_links(RESULTS)
        cls.output = RESULTS / ('workspace-create-' + uuid.uuid4().hex)
        cls.output.mkdir()
        cls.copy = cls.output / 'product copy'
        (cls.copy / 'scripts').mkdir(parents=True)
        shutil.copytree(PRODUCT / 'template', cls.copy / 'template')
        for ext in ('ps1', 'sh'):
            shutil.copyfile(PRODUCT / 'scripts' / ('new-project.' + ext), cls.copy / 'scripts' / ('new-project.' + ext))
        fixture = json.loads((ROOT / 'fixtures/workspace-create/template.json').read_text(encoding='utf-8'))
        for rel, raw in fixture['files_hex'].items():
            (cls.copy / 'template' / rel).write_bytes(bytes.fromhex(raw))
        (cls.copy / 'template' / fixture['unicode_path']).write_text(fixture['unicode_text'], encoding='utf-8', newline='')
        (cls.copy / 'template' / fixture['empty_directory']).mkdir()
        cls.original = snapshot(cls.copy)
        cls.source_before = snapshot(PRODUCT / 'template')
        cls.records = []
        def version(args):
            return subprocess.run(args, capture_output=True, timeout=20).stdout.decode('utf-8', 'replace').strip()
        provenance = {
            'mode': 'offline-subprocess', 'timeout_seconds': INVOCATION_WALL,
            'idle_seconds': INVOCATION_IDLE, 'stream_bytes': INVOCATION_STREAM, 'attempts': 1,
            'product_head': version(['git', '-C', str(PRODUCT), 'rev-parse', 'HEAD']),
            'harness_head': version(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']),
            'powershell': str(PS), 'powershell_version': version([str(PS), '-NoProfile', '-Command', '$PSVersionTable.PSVersion.ToString()']),
            'bash': str(BASH), 'bash_version': version([str(BASH), '--version']),
            'input_sha256': {k: digest(v) for k, v in cls.original.items() if v is not None},
            'fixture_sha256': digest((ROOT / 'fixtures/workspace-create/template.json').read_bytes()),
            'idle_renewal': 'Captured stream bytes and increased Windows Job write bytes.',
            'telemetry_only': 'Job reads, other I/O and process transitions.',
        }
        (cls.output / 'provenance.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
        print('\nCreation evidence:', cls.output, flush=True)

    def setUp(self):
        self.case = self.output / (self._testMethodName + '-' + uuid.uuid4().hex[:8])
        self.case.mkdir()

    def tearDown(self):
        result = self._outcome.result
        failed = any(test is self for test, _ in result.failures + result.errors)
        skipped = next((reason for test, reason in result.skipped if test is self), None)
        self.records.append({'case': self._testMethodName, 'status': 'FAIL' if failed else 'SKIP' if skipped else 'PASS', 'reason': skipped, 'evidence': self.case.name})

    @classmethod
    def tearDownClass(cls):
        unchanged = snapshot(PRODUCT / 'template') == cls.source_before and snapshot(cls.copy) == cls.original
        cls.records.append({'case': 'source_preservation', 'status': 'PASS' if unchanged else 'FAIL'})
        (cls.output / 'report.json').write_text(json.dumps({'cases': cls.records, 'overall': 'FAIL' if any(r['status'] == 'FAIL' for r in cls.records) else 'PASS', 'dimensions': {
            'preflight': 'Windows interpreters recorded', 'completion': 'exit and independent artifact assertions',
            'tool_use': 'actual subprocess arguments and outputs retained', 'evidence_integrity': 'input digests and output snapshots retained',
            'semantic_accuracy': 'byte equality and independent hashlib manifest oracle',
            'requirement_coverage': 'see case list and creation validation guide', 'provenance': 'provenance.json'}}, indent=2), encoding='utf-8')
        if not unchanged:
            raise AssertionError('Source changed')

    def invoke(self, kind, dest=None, dry=False, product=None, fault=None):
        product = product or self.copy
        script = product / 'scripts' / ('new-project.' + ('ps1' if kind == 'ps' else 'sh'))
        args = ([str(PS), '-NoProfile', '-File', str(script)] if kind == 'ps' else [str(BASH), str(script)])
        if dest is not None:
            args += (['-Destination', str(dest)] if kind == 'ps' else [str(dest)])
        if dry:
            args.append('-DryRun' if kind == 'ps' else '--dry-run')
        if fault == 'inspect':
            if kind == 'ps':
                wrapper = "function Get-ChildItem { throw 'simulated directory read denial' }; & '" + str(script).replace("'", "''") + "' -Destination '" + str(dest).replace("'", "''") + "'" + (' -DryRun' if dry else '')
                args = [str(PS), '-NoProfile', '-Command', wrapper]
            else:
                args = [str(BASH), '-c', 'find() { return 73; }; export -f find; exec bash "$@"', 'fault', str(script), str(dest)] + (['--dry-run'] if dry else [])
        elif fault:
            if kind == 'ps':
                text = script.read_text(encoding='utf-8').splitlines()
                needle = '[IO.File]::Copy' if fault == 'copy' else '[IO.Directory]::CreateDirectory($meta)'
                line = next(i + 1 for i, s in enumerate(text) if needle in s)
                target = '$dest' if fault == 'copy' else '$meta'
                # Debugger injects a competing file at a deterministic operation boundary.
                wrapper = "Set-PSBreakpoint -Script '" + str(script).replace("'", "''") + "' -Line " + str(line) + " -Action { [IO.File]::WriteAllText(" + target + ", 'sentinel') } | Out-Null; & '" + str(script).replace("'", "''") + "' -Destination '" + str(dest).replace("'", "''") + "'"
                args = [str(PS), '-NoProfile', '-Command', wrapper]
            else:
                # Override a bundled utility only in this subprocess, keeping Product bytes intact.
                command = 'cat' if fault == 'copy' else 'mkdir'
                body = 'return 73' if fault == 'copy' else 'case "$*" in *.codex-workspace*) return 73;; *) command mkdir "$@";; esac'
                wrapper = command + '() { ' + body + '; }; export -f ' + command + '; exec bash "$@"'
                args = [str(BASH), '-c', wrapper, 'fault', str(script), str(dest)]
        index = len(list(self.case.glob('invocation-*.jsonl')))
        evidence = self.case / f'invocation-{index}'
        # Preserve process-launch prerequisites without inheriting credentials,
        # shell startup hooks or provider configuration from the caller.
        allowed = {'path', 'pathext', 'systemroot', 'systemdrive', 'windir', 'comspec',
                   'temp', 'tmp', 'userprofile', 'localappdata', 'appdata',
                   'programfiles', 'programfiles(x86)', 'programdata', 'number_of_processors'}
        env = {key: value for key, value in os.environ.items() if key.lower() in allowed}
        with evidence.with_suffix('.jsonl').open('x', encoding='utf-8', newline='\n', buffering=1) as journal:
            def append(event, **fields):
                data = {'event': event, 'utc': datetime.now(timezone.utc).isoformat(), **fields}
                journal.write(json.dumps(data) + '\n')
                journal.flush()

            append('start', kind=kind, args=args, cwd=self.case.name,
                   wall_seconds=INVOCATION_WALL, idle_seconds=INVOCATION_IDLE,
                   stream_bytes=INVOCATION_STREAM)
            observation, stdout, stderr = run_process(
                args, self.case, env, INVOCATION_WALL, INVOCATION_IDLE,
                observe_progress=lambda event: append('progress', **event),
                renew_on_job_writes=True, stream_limit=INVOCATION_STREAM)
            evidence.with_suffix('.stdout').write_bytes(stdout)
            evidence.with_suffix('.stderr').write_bytes(stderr)
            append('finish', **observation, stdout_sha256=digest(stdout), stderr_sha256=digest(stderr))

        record = {'args': args, 'cwd': self.case.name, 'exit_code': observation['exit_code'],
                  'timed_out': observation['stop_reason'] in ('wall-timeout', 'idle-timeout'),
                  'observation': observation, 'stdout_sha256': digest(stdout),
                  'stderr_sha256': digest(stderr), 'stdout': stdout.decode('utf-8', 'replace'),
                  'stderr': stderr.decode('utf-8', 'replace')}
        evidence.with_suffix('.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
        self.assertIsNone(observation['stop_reason'], record)
        self.assertTrue(observation['job_drained'], record)
        return observation['exit_code'], record

    def verify(self, dest, source=None):
        expected = snapshot((source or self.copy) / 'template')
        actual = snapshot(dest)
        manifest_raw = actual.pop('.codex-workspace/manifest.json')
        self.assertIsNone(actual.pop('.codex-workspace'))
        self.assertEqual(expected, actual)
        manifest = json.loads(manifest_raw)
        files = {k: digest(v) for k, v in expected.items() if v is not None}
        paths = sorted(files, key=lambda s: s.encode('utf-8'))
        self.assertEqual(set(manifest), {'schema_version', 'template', 'files'})
        self.assertEqual(manifest['schema_version'], 1)
        self.assertEqual(manifest['files'], [{'path': p, 'sha256': files[p]} for p in paths])
        inventory = ''.join(p + '\t' + files[p] + '\n' for p in paths).encode('utf-8')
        self.assertEqual(manifest['template'], {'id': 'codex-workspace-template', 'content_sha256': digest(inventory)})
        self.assertFalse((dest / '.git').exists())
        self.assertFalse((dest / 'session_data').exists())
        self.assertEqual(8, len(list((dest / '.agents/skills').glob('*/SKILL.md'))))

    def test_creation_and_byte_parity(self):
        for scenario in ('new', 'empty', 'relative', 'windows', 'git-path'):
            destinations = []
            for kind in ('ps', 'bash'):
                dest = self.case / (scenario + ' ' + kind + ' caff\u00e8 \u65e5\u672c\u8a9e')
                if scenario == 'empty':
                    dest.mkdir()
                arg = dest.name if scenario == 'relative' else str(dest)
                if scenario == 'git-path' and kind == 'bash':
                    arg = '/' + str(dest)[0].lower() + str(dest)[2:].replace('\\', '/')
                code, record = self.invoke(kind, arg)
                self.assertEqual(code, 0, record)
                self.verify(dest)
                destinations.append(dest)
            self.assertEqual(snapshot(destinations[0]), snapshot(destinations[1]))

    def test_dry_run_no_writes(self):
        for kind in ('ps', 'bash'):
            for exists in (False, True):
                dest = self.case / (kind + str(exists))
                if exists:
                    dest.mkdir()
                before = {k: v for k, v in snapshot(self.case).items() if not k.startswith('invocation-')}
                code, record = self.invoke(kind, dest, dry=True)
                self.assertEqual(code, 0, record)
                after = snapshot(self.case)
                # Only the test driver evidence file is permitted to change.
                self.assertEqual(before, {k: v for k, v in after.items() if not k.startswith('invocation-')})
                self.assertEqual(dest.exists(), exists)

    def test_rejections_preserve_data(self):
        for kind in ('ps', 'bash'):
            occupied = self.case / (kind + '-occupied'); occupied.mkdir()
            (occupied / 'user.txt').write_bytes(b'user\x00data')
            collision = self.case / (kind + '-file'); collision.write_bytes(b'preserve')
            cases = [None, '', occupied, collision, self.copy, self.copy / 'template/inside',
                     self.case / 'x/../escape', self.case / 'NUL', self.case / 'trailing.', 'C:\\',
                     self.case / 'bad:stream', self.case / 'nonexistent/child/../../escape', 'C:relative', 'C:',
                     self.case / 'control\x7f', '   ']
            before = {k: v for k, v in snapshot(self.case).items() if not k.startswith('invocation-')}
            for dest in cases:
                code, record = self.invoke(kind, dest)
                self.assertNotEqual(code, 0, record)
            code, record = self.invoke(kind, occupied, dry=True)
            self.assertNotEqual(code, 0, record)
            after = snapshot(self.case)
            self.assertEqual(before, {k: v for k, v in after.items() if not k.startswith('invocation-')})

    def test_missing_reserved_and_linked_sources(self):
        for scenario in ('missing', 'reserved', 'junction'):
            copy = self.case / scenario
            shutil.copytree(self.copy / 'scripts', copy / 'scripts')
            if scenario != 'missing':
                shutil.copytree(self.copy / 'template', copy / 'template')
            if scenario == 'reserved':
                (copy / 'template/.codex-workspace').mkdir()
            if scenario == 'junction':
                self.junction(copy / 'template/linked', self.copy / 'template')
            for kind in ('ps', 'bash'):
                dest = self.case / (scenario + kind)
                code, record = self.invoke(kind, dest, product=copy)
                self.assertNotEqual(code, 0, record)
                self.assertFalse(dest.exists())

    def junction(self, link, target):
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_destination_junction(self):
        real = self.case / 'protected'; real.mkdir()
        (real / 'sentinel').write_bytes(b'outside')
        link = self.case / 'junction'
        self.junction(link, real)
        for kind in ('ps', 'bash'):
            for dest in (link, link / 'new'):
                code, record = self.invoke(kind, dest)
                self.assertNotEqual(code, 0, record)
        self.assertEqual({'sentinel': b'outside'}, snapshot(real))

    def test_optional_symlink(self):
        real = self.case / 'real'; real.mkdir()
        link = self.case / 'symlink'
        try:
            os.symlink(real, link, target_is_directory=True)
        except OSError as error:
            self.skipTest('Optional symbolic link creation unavailable: ' + str(error.winerror))
        for kind in ('ps', 'bash'):
            code, record = self.invoke(kind, link / 'new')
            self.assertNotEqual(code, 0, record)
        self.assertEqual({}, snapshot(real))

    def test_dangling_junction(self):
        missing = self.case / 'must-stay-absent'
        link = self.case / 'dangling'
        self.junction(link, missing)
        for kind in ('ps', 'bash'):
            for dest in (link, link / 'new'):
                code, record = self.invoke(kind, dest)
                self.assertNotEqual(code, 0, record)
        self.assertFalse(missing.exists())

    def test_simulated_copy_and_manifest_failures(self):
        for kind in ('ps', 'bash'):
            for fault in ('copy', 'manifest'):
                dest = self.case / (kind + fault)
                code, record = self.invoke(kind, dest, fault=fault)
                self.assertNotEqual(code, 0, record)
                self.assertFalse((dest / '.codex-workspace/manifest.json').is_file())
                self.assertIn('ERROR', record['stderr'])
                self.assertTrue(dest.is_dir(), 'partial evidence must remain')

    def test_uninspectable_destination_is_not_empty_success(self):
        for kind in ('ps', 'bash'):
            for dry in (False, True):
                dest = self.case / (kind + str(dry)); dest.mkdir()
                code, record = self.invoke(kind, dest, dry=dry, fault='inspect')
                self.assertNotEqual(code, 0, record)
                self.assertEqual({}, snapshot(dest))

@unittest.skipUnless(os.name == 'nt', 'SKIP: Windows Job Object required')
class NativeWatchdogTests(unittest.TestCase):
    def run_child(self, code, *, wall=12, idle=2, stream_limit=65536, marker_bytes=None):
        with tempfile.TemporaryDirectory(prefix='p2-watchdog-') as directory:
            marker = Path(directory) / 'progress'
            if marker_bytes is not None:
                marker.write_bytes(marker_bytes)
            events = []
            result = run_process(
                [sys.executable, '-I', '-B', '-c', code, str(marker)],
                directory, controlled_env(), wall, idle,
                observe_progress=events.append, renew_on_job_writes=True,
                stream_limit=stream_limit)
            self.assertTrue(result[0]['job_drained'], result[0])
            self.assertTrue(events)
            for previous, current in zip(events, events[1:]):
                self.assertTrue(any(current[key] > previous[key] for key in (
                    'read_bytes', 'written_bytes', 'other_io_bytes', 'processes_started',
                    'processes_exited', 'stdout_bytes', 'stderr_bytes')))
            return result, events, marker.read_bytes() if marker.is_file() else None

    def test_silent_descendant_advances(self):
        work = (
            'import sys,time; f=open(sys.argv[1],"wb",buffering=0); '
            '[(f.write(b"x"), time.sleep(.25)) for _ in range(14)]')
        code = (
            'import subprocess,sys; subprocess.run('
            f'[sys.executable, "-c", {work!r}, sys.argv[1]], check=True)')
        (result, stdout, stderr), events, artifact = self.run_child(code)
        self.assertGreater(result['elapsed_seconds'], 2)
        self.assertIsNone(result['stop_reason'])
        self.assertEqual((0, b'', b'', b'x' * 14), (result['exit_code'], stdout, stderr, artifact))
        terminal = result['job_counters']
        self.assertGreaterEqual(terminal['processes_started'], 3)
        self.assertEqual(terminal['processes_started'], terminal['processes_exited'])
        self.assertTrue(any(e['idle_renewed_by_job_write'] for e in events))

    def test_repetitive_reads_do_not_renew_idle(self):
        code = (
            'import sys,time; f=open(sys.argv[1],"rb",buffering=0); '
            'exec("while True:\\n f.seek(0); f.read(1); time.sleep(.12)")')
        (result, stdout, stderr), events, artifact = self.run_child(
            code, wall=6, marker_bytes=b'x')
        self.assertEqual(('idle-timeout', 124), (result['stop_reason'], result['exit_code']))
        self.assertEqual((b'', b'', b'x'), (stdout, stderr, artifact))
        self.assertEqual(0, result['job_counters']['written_bytes'])
        self.assertFalse(any(event['idle_renewed_by_job_write'] for event in events))
        # At least two read increases after startup show the repeated work was sampled.
        reads = [event['read_bytes'] for event in events if event['read_bytes']]
        self.assertGreaterEqual(len(set(reads)), 3)

    def test_stalled_tree_and_partial_streams(self):
        code = (
            'import subprocess,sys,time; '
            'subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
            'print("before stall",flush=True); print("partial",file=sys.stderr,flush=True); '
            'time.sleep(60)')
        (result, stdout, stderr), events, _ = self.run_child(code)
        self.assertEqual(('idle-timeout', 124), (result['stop_reason'], result['exit_code']))
        self.assertIn(b'before stall', stdout)
        self.assertIn(b'partial', stderr)
        self.assertGreaterEqual(max(e['processes_started'] for e in events), 3)

    def test_active_tree_still_hits_wall(self):
        code = (
            'import sys,time; f=open(sys.argv[1],"wb",buffering=0); '
            '[(f.write(b"x"), time.sleep(.15)) for _ in range(400)]')
        (result, _, _), _, artifact = self.run_child(code, wall=3, idle=2)
        self.assertEqual(('wall-timeout', 124), (result['stop_reason'], result['exit_code']))
        self.assertGreater(len(artifact), 2)
        self.assertLess(len(artifact), 400)

    def test_stream_limit_drains_and_keeps_prefix(self):
        code = 'import sys,time; sys.stdout.buffer.write(b"x"*8192); sys.stdout.flush(); time.sleep(60)'
        (result, stdout, _), _, _ = self.run_child(code, stream_limit=1024)
        self.assertEqual(('stream-limit', 124), (result['stop_reason'], result['exit_code']))
        self.assertTrue(result['stream_limit_reached'])
        self.assertEqual(b'x' * 1024, stdout)


if __name__ == '__main__':
    unittest.main()
