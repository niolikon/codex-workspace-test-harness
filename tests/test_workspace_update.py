"""Native workspace update with independent byte/hash/state oracles; no inference."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
import uuid

from tests.test_workspace_create import PS, BASH, PRODUCT, ROOT, digest, snapshot
from runner.paths import RESULTS, no_links


PATH_BUDGET = 240
# Reserve the actual GUID and longest intermediate payload prefix used by Product.
BACKUP_PAYLOAD = Path('.codex-workspace/backups') / ('0' * 32) / 'replaced'


def measure_path(path):
    # Win32 limits count UTF-16 code units.
    return len(str(path).encode('utf-16-le')) // 2


def check_budget(*paths):
    longest = max(map(measure_path, paths))
    if longest > PATH_BUDGET:
        raise RuntimeError(f'BLOCKED: Generated workspace path needs {longest} UTF-16 units; '
                           f'budget is {PATH_BUDGET}. Select a short TEMP directory.')
    return longest


def manifest(files):
    paths = sorted(files, key=lambda s: s.encode('utf-8'))
    hashes = {p: digest(files[p]) for p in paths}
    inv = ''.join(p + '\t' + hashes[p] + '\n' for p in paths).encode()
    rows = ',\n'.join('    {"path":' + json.dumps(p, ensure_ascii=False) + ',"sha256":"' + hashes[p] + '"}' for p in paths)
    return ('{\n  "schema_version":1,\n  "template":{"id":"codex-workspace-template","content_sha256":"' + digest(inv) + '"},\n  "files": [\n' + rows + '\n  ]\n}\n').encode()


@unittest.skipUnless(PRODUCT is not None, 'NOT_RUN: select CODEX_PRODUCT_ROOT')
class UpdateProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != 'nt' or not PS.is_file() or not BASH.is_file():
            raise RuntimeError('BLOCKED: actual Windows PowerShell and Git Bash required')
        no_links(PRODUCT); no_links(RESULTS)
        temp = no_links(Path(tempfile.gettempdir()))
        if any(temp.is_relative_to(source.resolve()) for source in (ROOT, PRODUCT, RESULTS)):
            raise RuntimeError('BLOCKED: P3 requires TEMP outside the checkouts and results')
        # Check the longest planned layout before creating either run directory.
        projected = temp / 'p3-xxxxxxxx/00/bash-12345678 caff\u00e8 \u65e5\u672c\u8a9e'
        check_budget(projected / 'P2 workspace' / BACKUP_PAYLOAD / 'new caff\u00e8 \u65e5\u672c\u8a9e.bin')
        cls.stage = Path(tempfile.mkdtemp(prefix='p3-', dir=temp))
        no_links(cls.stage)
        cls.output = RESULTS / ('workspace-update-' + uuid.uuid4().hex)
        cls.output.mkdir()
        cls.cases_started = 0
        cls.source_before = snapshot(PRODUCT)
        cls.fixture_before = snapshot(ROOT / 'fixtures/workspace-update')
        cls.matrix = json.loads((ROOT / 'fixtures/workspace-update/matrix.json').read_text())
        cls.records = []
        provenance = {'product_head': cls.git(PRODUCT, 'rev-parse', 'HEAD'),
                      'harness_head': cls.git(ROOT, 'rev-parse', 'HEAD'),
                      'product_status': cls.git(PRODUCT, 'status', '--porcelain=v1'),
                      'harness_status': cls.git(ROOT, 'status', '--porcelain=v1'),
                      'timeout_seconds': 45, 'attempts': 1, 'mode': 'offline-native-subprocess',
                      'staging_root': str(cls.stage), 'path_budget_utf16_units': PATH_BUDGET,
                      'inputs': {str(p.relative_to(PRODUCT)): digest(p.read_bytes()) for p in (PRODUCT / 'scripts').glob('*') if p.is_file()},
                      'fixture_sha256': digest((ROOT / 'fixtures/workspace-update/matrix.json').read_bytes())}
        for name, args in [('powershell', [str(PS), '-NoProfile', '-Command', '$PSVersionTable.PSVersion.ToString()']), ('git_bash', [str(BASH), '--version'])]:
            provenance[name] = {'path': args[0], 'version': subprocess.run(args, capture_output=True, timeout=20).stdout.decode('utf-8', 'replace')}
        (cls.output / 'provenance.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')

    @staticmethod
    def git(root, *args):
        return subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True, timeout=20).stdout.decode('utf-8')

    @classmethod
    def tearDownClass(cls):
        unchanged = snapshot(PRODUCT) == cls.source_before and snapshot(ROOT / 'fixtures/workspace-update') == cls.fixture_before
        (cls.output / 'invocations.json').write_text(json.dumps(cls.records, indent=2), encoding='utf-8')
        (cls.output / 'integrity.json').write_text(json.dumps({'source_and_committed_fixtures_unchanged': unchanged}), encoding='utf-8')
        if not unchanged:
            raise AssertionError('Source/fixture integrity failure')

    def setUp(self):
        self.evidence = self.output / self._testMethodName
        self.evidence.mkdir()
        cls = type(self)
        self.case = cls.stage / f'{cls.cases_started:02d}'
        self.case.mkdir()
        cls.cases_started += 1
        (self.evidence / 'layout.json').write_text(
            json.dumps({'cwd': str(self.case), 'path_budget_utf16_units': PATH_BUDGET}),
            encoding='utf-8')

    def prepare(self, kind, rows=None):
        root = self.case / (kind + '-' + uuid.uuid4().hex[:8] + ' caff\u00e8 \u65e5\u672c\u8a9e')
        product = root / 'product'; workspace = root / 'workspace'
        base = {'AGENTS.md': b'agents\n'}; local = dict(base); new = dict(base)
        for row in rows or []:
            rel = 'files/' + row['name'] + '.txt'
            for key, target in [('b', base), ('l', local), ('n', new)]:
                if row[key] is not None:
                    target[rel] = row[key].encode()
        check_budget(*(product / 'scripts' / path.name for path in (PRODUCT / 'scripts').iterdir()),
                     *(workspace / BACKUP_PAYLOAD / rel for rel in base.keys() | local.keys() | new.keys()))
        shutil.copytree(PRODUCT / 'scripts', product / 'scripts')
        (product / 'template').mkdir(); workspace.mkdir()
        self.write(product / 'template', new); self.write(workspace, local)
        self.write(workspace, {'.codex-workspace/manifest.json': manifest(base),
                               'session_data/result.bin': b'prior\x00data', 'notes/user.txt': b'user'})
        return product, workspace, base, new

    @staticmethod
    def write(root, files):
        for rel, raw in files.items():
            path = root / rel; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)

    def invoke(self, kind, product, workspace, *options, fault=None, path_form=None):
        path = str(workspace)
        if path_form == 'git':
            path = '/' + path[0].lower() + path[2:].replace('\\', '/')
        if path_form == 'relative':
            path = os.path.relpath(workspace, self.case)
        if kind == 'ps':
            args = [str(PS), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(product / 'scripts/update-project.ps1'), '-Workspace', path]
            args += [{'--apply': '-Apply', '--dry-run': '-DryRun'}.get(o, o) for o in options]
        else:
            args = [str(BASH), str(product / 'scripts/update-project.sh'), '--workspace', path, *options]
        # Include the projected Product-generated staging and backup paths.
        longest = check_budget(self.case, *(Path(arg) for arg in args if len(arg) > 2),
                               *(workspace / BACKUP_PAYLOAD / p.relative_to(product / 'template')
                                 for p in (product / 'template').rglob('*')))
        if fault:
            engine = product / 'scripts/update-project.ps1'
            lines = engine.read_text().splitlines()
            needle, action = fault
            line = next(i+1 for i, s in enumerate(lines) if needle in s)
            quote = lambda v: "'" + str(v).replace("'", "''") + "'"
            wrapper = self.case / ('debug-' + uuid.uuid4().hex + '.ps1')
            longest = max(longest, check_budget(wrapper))
            wrapper.write_text('Set-PSBreakpoint -Script ' + quote(engine) + ' -Line ' + str(line) + ' -Action { ' + action + ' } | Out-Null\n& ' + quote(engine) + ' -Workspace ' + quote(workspace) + ' -Apply\nexit $LASTEXITCODE\n', encoding='utf-8-sig')
            if kind == 'ps':
                args = [str(PS), '-NoProfile', '-NonInteractive', '-File', str(wrapper)]
            else:
                # Intercept exec only in the fault subprocess to attach the native debugger.
                env = dict(os.environ, P3_DEBUG_WRAPPER=str(wrapper))
                args = [str(BASH), '-c', 'exec() { command "$1" -NoProfile -NonInteractive -File "$P3_DEBUG_WRAPPER"; }; source "$1" --workspace "$2" --apply', 'debug', str(product / 'scripts/update-project.sh'), path]
        process = subprocess.Popen(args, cwd=self.case, env=env if fault and kind == 'bash' else None, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        timed_out = False
        try:
            out, err = process.communicate(timeout=45)
        except subprocess.TimeoutExpired:
            timed_out = True
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10)
            out, err = process.communicate(timeout=10)
        record = {'kind': kind, 'case': self._testMethodName, 'cwd': str(self.case), 'args': args,
                  'longest_checked_path_utf16_units': longest, 'fault': fault, 'timed_out': timed_out,
                  'exit_code': process.returncode, 'stdout': out[:65536].decode('utf-8', 'replace'), 'stderr': err[:65536].decode('utf-8', 'replace')}
        self.records.append(record)
        (self.evidence / ('invocation-' + str(len(self.records)) + '.json')).write_text(json.dumps(record, indent=2), encoding='utf-8')
        self.assertFalse(timed_out, record)
        return process.returncode, record

    def test_matrix_success_preview_apply_idempotence_parity(self):
        rows = [r for r in self.matrix if not r['action'].startswith('CONFLICT')]
        outputs = []
        for kind in ('ps', 'bash'):
            with self.subTest(kind=kind):
                product, ws, base, new = self.prepare(kind, rows)
                before = snapshot(ws)
                times = {p.relative_to(ws).as_posix(): p.stat().st_mtime_ns for p in ws.rglob('*') if p.is_file()}
                for args in [(), ('--dry-run',)]:
                    rc, rec = self.invoke(kind, product, ws, *args)
                    self.assertEqual(0, rc, rec); self.assertEqual(before, snapshot(ws))
                    self.assertEqual(times, {p.relative_to(ws).as_posix(): p.stat().st_mtime_ns for p in ws.rglob('*') if p.is_file()})
                    for row in rows:
                        self.assertIn(row['action'] + ' files/' + row['name'] + '.txt', rec['stdout'])
                rc, rec = self.invoke(kind, product, ws, '--apply')
                self.assertEqual(0, rc, rec)
                expected_base = {'AGENTS.md': b'agents\n'}
                for row in rows:
                    rel = 'files/' + row['name'] + '.txt'; path = ws / rel
                    self.assertEqual(row['after'].encode() if row['after'] is not None else None, path.read_bytes() if path.exists() else None)
                    if row['baseline'] is not None:
                        expected_base[rel] = row['baseline'].encode()
                self.assertEqual(manifest(expected_base), (ws / '.codex-workspace/manifest.json').read_bytes())
                backups = list((ws / '.codex-workspace/backups').glob('*/replaced/**/*'))
                self.assertEqual({'files/update.txt': b'base'}, {p.relative_to(next(p for p in p.parents if p.name == 'replaced')).as_posix(): p.read_bytes() for p in backups if p.is_file()})
                self.assertEqual(b'prior\x00data', (ws / 'session_data/result.bin').read_bytes())
                self.assertEqual(b'user', (ws / 'notes/user.txt').read_bytes())
                for rel in times:
                    if rel != '.codex-workspace/manifest.json' and rel != 'files/update.txt':
                        self.assertEqual(times[rel], (ws/rel).stat().st_mtime_ns, rel)
                after = snapshot(ws)
                after_times = {p.relative_to(ws).as_posix(): p.stat().st_mtime_ns for p in ws.rglob('*') if p.is_file()}
                rc, rec = self.invoke(kind, product, ws, '--apply')
                self.assertEqual(0, rc, rec); self.assertEqual(after, snapshot(ws))
                self.assertEqual(after_times, {p.relative_to(ws).as_posix(): p.stat().st_mtime_ns for p in ws.rglob('*') if p.is_file()})
                outputs.append({k:v for k,v in after.items() if not k.startswith('.codex-workspace/backups')})
        self.assertEqual(2, len(outputs))
        if len(outputs) == 2:
            self.assertEqual(outputs[0], outputs[1])


    def test_manifest_only_release_reintroduction_and_empty_base(self):
        for kind in ('ps','bash'):
            for scenario in ('align','release','empty','formatted-noop','punctuation'):
                with self.subTest(kind=kind,scenario=scenario):
                    rows = [next(r for r in self.matrix if r['action']=='ALIGN')] if scenario=='align' else []
                    product,ws,base,new=self.prepare(kind,rows)
                    mp=ws/'.codex-workspace/manifest.json'
                    if scenario=='empty': mp.write_bytes(manifest({})); (ws/'AGENTS.md').unlink()
                    if scenario=='formatted-noop': mp.write_text(json.dumps(json.loads(mp.read_bytes()),indent=4),encoding='utf-8')
                    if scenario=='release':
                        self.write(ws,{'released.txt':b'local'})
                        base['released.txt']=b'base'; mp.write_bytes(manifest(base))
                    if scenario=='punctuation':
                        # Legal P2 names must not trigger pointless manifest normalization.
                        rel="odd & 'name.txt"; base[rel]=b'bytes'; new[rel]=b'bytes'
                        self.write(ws,{rel:b'bytes'}); self.write(product/'template',{rel:b'bytes'}); mp.write_bytes(manifest(base))
                    before=snapshot(ws)
                    rc,rec=self.invoke(kind,product,ws,'--apply'); self.assertEqual(0,rc,rec)
                    if scenario in ('formatted-noop','punctuation'):
                        self.assertEqual(before,snapshot(ws)); self.assertFalse((ws/'.codex-workspace/backups').exists())
                    else:
                        self.assertFalse(list((ws/'.codex-workspace/backups').glob('*/replaced/**/*')))
                        if scenario=='release':
                            self.assertEqual(b'local',(ws/'released.txt').read_bytes())
                            self.assertNotIn('released.txt',{r['path'] for r in json.loads(mp.read_bytes())['files']})
                            self.write(product/'template',{'released.txt':b'local'})
                            after=snapshot(ws); rc,rec=self.invoke(kind,product,ws,'--apply')
                            self.assertNotEqual(0,rc,rec); self.assertEqual(after,snapshot(ws))
                            self.assertIn('CONFLICT_UNMANAGED released.txt',rec['stdout'])
                        else:
                            expected=new
                            self.assertEqual(manifest(expected),mp.read_bytes())

    def test_all_conflicts_block_entire_apply(self):
        rows = [r for r in self.matrix if r['action'].startswith('CONFLICT') or r['action'] == 'UPDATE']
        for kind in ('ps', 'bash'):
            product, ws, _, _ = self.prepare(kind, rows); before = snapshot(ws)
            for opts in [(), ('--apply',)]:
                rc, rec = self.invoke(kind, product, ws, *opts)
                self.assertNotEqual(0, rc, rec); self.assertEqual(before, snapshot(ws))
                for row in rows:
                    self.assertIn(row['action'] + ' files/' + row['name'] + '.txt', rec['stdout'])

    def test_invalid_manifests_and_topology_preserve_all(self):
        for kind in ('ps', 'bash'):
            for scenario in ('missing','json','schema','digest','duplicate-key','duplicate-path','case','unordered','traversal','absolute','reserved','backslash','directory','parent-file','unknown','hash','escaped-duplicate','prefix'):
                with self.subTest(kind=kind, scenario=scenario):
                    product, ws, base, _ = self.prepare(kind)
                    mp = ws / '.codex-workspace/manifest.json'
                    m = json.loads(mp.read_bytes())
                    if scenario == 'missing': mp.unlink()
                    elif scenario == 'json': mp.write_text('{')
                    elif scenario == 'duplicate-key': mp.write_bytes(mp.read_bytes().replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'))
                    elif scenario == 'escaped-duplicate': mp.write_bytes(mp.read_bytes().replace(b'"schema_version":1', b'"schema_version":1,"schema_versio\\u006e":1'))
                    elif scenario == 'directory': (ws / 'AGENTS.md').unlink(); (ws / 'AGENTS.md').mkdir()
                    elif scenario == 'parent-file': self.write(product / 'template', {'notes/user.txt/child':b'n'})
                    else:
                        if scenario == 'schema': m['schema_version'] = True
                        if scenario == 'digest': m['template']['content_sha256'] = '0'*64
                        if scenario == 'unknown': m['extra'] = 1
                        if scenario == 'hash': m['files'][0]['sha256'] = 'x'*64
                        if scenario == 'duplicate-path': m['files'] *= 2
                        if scenario == 'case': m['files'].append({'path':'agents.md','sha256':digest(b'a')})
                        if scenario == 'unordered': mp.write_bytes(manifest({'z':b'a','AGENTS.md':b'a'})); m=json.loads(mp.read_bytes()); m['files'].reverse()
                        paths={'traversal':'../outside','absolute':'C:/outside','reserved':'session_data/a','backslash':'a\\b'}
                        if scenario in paths: m['files'][0]['path'] = paths[scenario]
                        if scenario == 'prefix': mp.write_bytes(manifest({'AGENTS.md':b'agents\n','a':b'a','a/b':b'b'})); m=json.loads(mp.read_bytes())
                        mp.write_text(json.dumps(m),encoding='utf-8')
                    before = snapshot(ws)
                    rc, rec = self.invoke(kind, product, ws, '--apply')
                    self.assertNotEqual(0, rc, rec); self.assertEqual(before, snapshot(ws))

    def test_paths_and_created_workspaces(self):
        for kind in ('ps','bash'):
            for form in ('relative','windows','git') if kind == 'bash' else ('relative','windows'):
                product, ws, _, _ = self.prepare(kind)
                # Real P2 creation, then a P3 addition: no handmade-manifest dependency.
                fresh = ws.parent / 'P2 workspace'
                args = [str(PS),'-NoProfile','-File',str(product/'scripts/new-project.ps1'),'-Destination',str(fresh)] if kind == 'ps' else [str(BASH),str(product/'scripts/new-project.sh'),str(fresh)]
                result = subprocess.run(args, capture_output=True, timeout=45)
                (self.evidence / ('p2-' + uuid.uuid4().hex + '.json')).write_text(json.dumps({'args':args,'exit_code':result.returncode,'stdout':result.stdout.decode('utf-8','replace'),'stderr':result.stderr.decode('utf-8','replace')}))
                self.assertEqual(0,result.returncode,result.stderr)
                self.write(product/'template',{'new caff\u00e8 \u65e5\u672c\u8a9e.bin':b'\x00\xff\r\n'})
                rc, rec = self.invoke(kind,product,fresh,'--apply',path_form=form)
                self.assertEqual(0,rc,rec)
                self.assertEqual(b'\x00\xff\r\n',(fresh/'new caff\u00e8 \u65e5\u672c\u8a9e.bin').read_bytes())
                self.assertEqual(manifest({'AGENTS.md':b'agents\n','new caff\u00e8 \u65e5\u672c\u8a9e.bin':b'\x00\xff\r\n'}),(fresh/'.codex-workspace/manifest.json').read_bytes())

    def test_drift_and_partial_write_recovery_evidence(self):
        row = next(r for r in self.matrix if r['action']=='UPDATE')
        for kind in ('ps','bash'):
            for fault in ('drift','source-drift','manifest-drift','late-drift','partial','manifest-failure'):
                product, ws, base, new = self.prepare(kind,[row])
                self.write(product/'template',{'z-last.txt':b'last'})
                before = (ws/'.codex-workspace/manifest.json').read_bytes()
                if fault == 'drift':
                    injection = ('    Revalidate', "[IO.File]::WriteAllText((Join-Path $target 'files/update.txt'), 'drift')")
                elif fault == 'source-drift':
                    injection = ('    Revalidate', "[IO.File]::WriteAllText((Join-Path $source 'z-last.txt'), 'drift')")
                elif fault == 'manifest-drift':
                    injection = ('    Revalidate', "[IO.File]::AppendAllText($manifestPath, ' ')")
                elif fault == 'late-drift':
                    injection = ("    $phase = 'applying'", "[IO.File]::WriteAllText((Join-Path $target 'files/update.txt'), 'drift')")
                elif fault == 'manifest-failure':
                    injection = ("    $phase = 'manifest commit'", "[IO.File]::Move((Join-Path $run 'manifest.after.json'), (Join-Path $run 'held-manifest.json'))")
                else:
                    injection = ('        $dest = Join-Path $target $rel', "if ($rel -eq 'z-last.txt') { [IO.File]::Move((Join-Path $run ('staged/' + $rel)), (Join-Path $run 'held-last.txt')) }")
                rc, rec = self.invoke(kind,product,ws,'--apply',fault=injection)
                self.assertNotEqual(0,rc,rec)
                expected_manifest = before + b' ' if fault == 'manifest-drift' else before
                self.assertEqual(expected_manifest,(ws/'.codex-workspace/manifest.json').read_bytes())
                if fault in ('drift','late-drift'):
                    self.assertEqual(b'drift',(ws/'files/update.txt').read_bytes())
                    if fault == 'drift': self.assertFalse((ws/'.codex-workspace/backups').exists())
                elif fault in ('source-drift','manifest-drift'):
                    self.assertEqual(b'base',(ws/'files/update.txt').read_bytes())
                    self.assertFalse((ws/'.codex-workspace/backups').exists())
                else:
                    self.assertEqual(b'new',(ws/'files/update.txt').read_bytes())
                    if fault == 'manifest-failure': self.assertEqual(b'last',(ws/'z-last.txt').read_bytes())
                    else: self.assertFalse((ws/'z-last.txt').exists())
                    backups=list((ws/'.codex-workspace/backups').glob('*/replaced/files/update.txt'))
                    self.assertEqual([b'base'],[p.read_bytes() for p in backups])
                    self.assertIn('Partial artifacts possible: True',rec['stderr'])

    def test_junctions_and_dangling_junctions(self):
        for kind in ('ps','bash'):
            for location in ('workspace','managed','source','metadata','backup','dangling'):
                product, ws, _, _ = self.prepare(kind)
                outside=ws.parent/'outside'; outside.mkdir(); (outside/'sentinel').write_bytes(b'protected')
                if location == 'workspace': link=ws.parent/'link'; invocation=link
                else:
                    invocation=ws
                    link={'managed':ws/'linked','source':product/'template/linked','metadata':ws/'.codex-workspace','backup':ws/'.codex-workspace/backups','dangling':ws/'linked'}[location]
                if location=='metadata': (link/'manifest.json').unlink(); link.rmdir()
                if location in ('managed','dangling'): self.write(product/'template',{'linked/file.txt':b'n'})
                if location=='backup': self.write(product/'template',{'new.txt':b'n'})
                target=ws if location=='workspace' else outside
                if location=='dangling': target=ws.parent/'missing'
                result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(target)],capture_output=True,timeout=10)
                self.assertEqual(0,result.returncode,result.stderr)
                rc,rec=self.invoke(kind,product,invocation,'--apply')
                self.assertNotEqual(0,rc,rec)
                self.assertEqual({'sentinel':b'protected'},snapshot(outside))

    def test_optional_symlink(self):
        product,ws,_,_=self.prepare('symlink')
        outside=ws.parent/'outside'; outside.mkdir()
        try: os.symlink(outside,ws/'linked',target_is_directory=True)
        except OSError as error: self.skipTest('Optional symlink privilege unavailable: '+str(error.winerror))
        self.write(product/'template',{'linked/file':b'n'})
        for kind in ('ps','bash'):
            rc,rec=self.invoke(kind,product,ws,'--apply'); self.assertNotEqual(0,rc,rec)
        self.assertEqual({},snapshot(outside))

    def test_cli_rejections_no_writes(self):
        for kind in ('ps','bash'):
            product,ws,_,_=self.prepare(kind); before=snapshot(ws)
            for opts in [('--apply','--dry-run'),('--unknown',)]:
                rc,rec=self.invoke(kind,product,ws,*opts); self.assertNotEqual(0,rc,rec)
                self.assertEqual(before,snapshot(ws))
            for path in [product,ws/'x/../escape','C:relative','C:\\']:
                rc,rec=self.invoke(kind,product,path,'--apply'); self.assertNotEqual(0,rc,rec)
                self.assertEqual(before,snapshot(ws))


if __name__ == '__main__': unittest.main()
