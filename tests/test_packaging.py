"""Packaging and result contract checks; no inference, network or workflow execution."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = Path(os.environ['CODEX_PRODUCT_ROOT']).resolve() if os.environ.get('CODEX_PRODUCT_ROOT') else None
STATUSES = {'PASS', 'FAIL', 'BLOCKED', 'NOT_RUN', 'SKIP'}


def validate(value, schema):
    """Restricted foundation vocabulary, not a general JSON Schema engine."""
    allowed = {'$schema', 'title', 'type', 'properties', 'required', 'additionalProperties', 'items', 'enum', 'const', 'minLength', 'pattern'}
    if set(schema) - allowed:
        raise ValueError('Unsupported schema vocabulary')
    kind = schema.get('type')
    types = {'object': dict, 'array': list, 'string': str}
    if kind and (kind not in types or not isinstance(value, types[kind])):
        raise ValueError('Type mismatch')
    if 'const' in schema and (type(value) is not type(schema['const']) or value != schema['const']):
        raise ValueError('Constant mismatch')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Unknown enum')
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or ('pattern' in schema and re.search(schema['pattern'], value) is None):
            raise ValueError('Invalid string')
    if kind == 'object':
        props = schema['properties']
        if set(schema['required']) - value.keys() or (schema.get('additionalProperties') is False and value.keys() - props.keys()):
            raise ValueError('Invalid properties')
        for key, item in value.items():
            if key in props:
                validate(item, props[key])
    if kind == 'array':
        for item in value:
            validate(item, schema['items'])


def source_files(root):
    # Packaging concerns versionable sources, not ignored editor/credential data.
    files = subprocess.run(['git', '-C', str(root), 'ls-files', '--cached', '--others',
                            '--exclude-standard', '-z'], check=True, capture_output=True)
    return [root / name for name in files.stdout.decode('utf-8').split('\0') if name]


def check_root(test, root):
    for name in ['AGENTS.md', 'README.md', 'GUIDE.md', 'ARCHITECTURE.md', 'CHANGELOG.md', 'LICENSE', '.gitignore', '.gitattributes']:
        test.assertTrue((root/name).is_file(), name)
    git = subprocess.run(['git', '-C', str(root), 'rev-parse', '--show-toplevel'], check=True, capture_output=True, text=True)
    test.assertEqual(root.resolve(), Path(git.stdout.strip()).resolve())
    test.assertEqual([root/'.git'], list(root.rglob('.git')))
    license_text = (root/'LICENSE').read_text(encoding='utf-8')
    test.assertIn('Copyright (c) 2026 Simone Andrea Muscas', license_text)
    test.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', license_text)
    for path in source_files(root):
        raw = path.read_bytes()
        test.assertNotIn(b'\r', raw, path.name)
        text = raw.decode('utf-8')
        test.assertNotIn('\ufeff', text, path.name)
        if path.suffix == '.md':
            for pattern in [r'[A-Za-z]:[\\/]Users[\\/]', r'/home/[^/ ]+/', r'https?://(?:localhost|127\.0\.0\.1|[^/ ]+\.internal)(?:/|\b)', r'-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----', r'gh[pousr]_[A-Za-z0-9]{20,}', r'sk-[A-Za-z0-9]{20,}']:
                test.assertIsNone(re.search(pattern, text), path.name)
            # Scenario Markdown describes paths in its disposable workspace.
            if path.is_relative_to(root/'fixtures') and path.name != 'README.md':
                continue
            for link in re.findall(r'(?<!!)\[[^]\n]+\]\(([^)]+)\)', text):
                if re.match(r'[a-z]+://', link):
                    continue
                target = link.split('#',1)[0]
                if target:
                    test.assertTrue((path.parent/target).exists(), (str(path.relative_to(root)), link))


class HarnessFoundation(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads((ROOT/'contracts/case-result.schema.json').read_text(encoding='utf-8'))
        self.base = json.loads((ROOT/'fixtures/results/pass.json').read_text(encoding='utf-8'))

    def test_harness_packaging(self):
        check_root(self, ROOT)
        for name in ['docs/execution.md','docs/results.md','docs/validation.md','contracts/README.md','fixtures/README.md','runner/README.md']:
            self.assertTrue((ROOT/name).is_file(), name)

    def test_five_status_examples(self):
        seen = set()
        for path in (ROOT/'fixtures/results').glob('*.json'):
            value = json.loads(path.read_text(encoding='utf-8'))
            validate(value, self.schema)
            seen.add(value['overall']['status'])
        self.assertEqual(STATUSES, seen)

    def test_negative_structure(self):
        bad_values = []
        bad = copy.deepcopy(self.base); del bad['dimensions']['provenance']; bad_values.append(bad)
        bad = copy.deepcopy(self.base); bad['overall']['status'] = 'SUCCESS'; bad_values.append(bad)
        bad = copy.deepcopy(self.base); bad['provenance']['fixture_sha256'] = 'unverified'; bad_values.append(bad)
        bad = copy.deepcopy(self.base); bad['extra'] = True; bad_values.append(bad)
        bad = copy.deepcopy(self.base); bad['schema_version'] = True; bad_values.append(bad)
        bad = copy.deepcopy(self.base); bad['overall']['reason'] = ''; bad_values.append(bad)
        for index, bad in enumerate(bad_values):
            with self.subTest(example=index), self.assertRaises(ValueError):
                validate(bad, self.schema)
        with self.assertRaises(ValueError):
            validate(self.base, {**self.schema, 'unsupported': True})

    def test_structure_does_not_prove_acceptance(self):
        self.base['dimensions']['semantic_accuracy']['status'] = 'FAIL'
        # Contradictory overall PASS remains structurally valid. Future independent
        # oracles must reject acceptance; this checker does not evaluate semantics.
        validate(self.base, self.schema)
        self.assertNotEqual(self.base['overall']['status'], self.base['dimensions']['semantic_accuracy']['status'])


@unittest.skipUnless(PRODUCT is not None, 'NOT_RUN: set CODEX_PRODUCT_ROOT for explicit Product checks')
class ProductFoundation(unittest.TestCase):
    def test_product_packaging(self):
        check_root(self, PRODUCT)
        self.assertTrue((PRODUCT/'docs/validation.md').is_file(), 'docs/validation.md')
        self.assertNotEqual(PRODUCT.resolve(), ROOT.resolve())
        self.assertEqual((PRODUCT/'LICENSE').read_bytes(), (ROOT/'LICENSE').read_bytes())
        for name in ['documents','knowledge-base','notes','repositories','trainings','.codex/agents']:
            self.assertTrue((PRODUCT/'template'/name/'.gitkeep').is_file(), name)
        for name in ['template/AGENTS.md','template/.gitignore','scripts/README.md','tests/README.md']:
            self.assertTrue((PRODUCT/name).is_file(), name)
        for path in source_files(PRODUCT):
            self.assertNotIn('session_data', path.relative_to(PRODUCT).parts)
            self.assertNotIn('providers', path.relative_to(PRODUCT).parts)
        self.assertFalse((PRODUCT/'template/scripts').exists())
        self.assertEqual([], list((PRODUCT/'template/.codex/agents').glob('*.toml')))
        self.assertEqual({'new-project.ps1', 'new-project.sh', 'update-project.ps1', 'update-project.sh'},
                         {p.name for ext in ('*.ps1', '*.sh') for p in (PRODUCT/'scripts').glob(ext)})

    def test_skill_frontmatter(self):
        skills = list(PRODUCT.glob('**/SKILL.md'))
        self.assertEqual(9, len(skills))
        expected = {'ask', 'knowledge', 'knowledge-init', 'knowledge-update',
                    'knowledge-curate', 'refine', 'implement', 'review'}
        self.assertEqual(expected, {p.parent.name for p in (PRODUCT/'template/.agents/skills').glob('*/SKILL.md')})
        for path in skills:
            with self.subTest(skill=path.parent.name):
                match = re.match(r'\A---\nname: ([a-z0-9]+(?:-[a-z0-9]+)*)\ndescription: ("[^\n]+")\n---\n', path.read_text(encoding='utf-8'))
                self.assertIsNotNone(match)
                self.assertEqual(path.parent.name, match[1])
                self.assertLess(len(match[1]),64)
                self.assertTrue(json.loads(match[2]).strip())

    def test_distribution_contracts(self):
        refs = list((PRODUCT/'template/.agents/skills/_contracts').glob('*.md'))
        self.assertEqual(12,len(refs))
        for path in refs:
            self.assertEqual((PRODUCT/'docs'/path.name).read_bytes(), path.read_bytes(), path.name)


if __name__ == '__main__':
    unittest.main()
