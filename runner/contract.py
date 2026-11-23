"""Strict H1 JSON contract, intentionally not a general JSON Schema engine."""
import json
import math
import re
from .paths import FIXTURES, local, relative

KINDS = {'exists', 'absent', 'text', 'json', 'tree', 'unchanged'}


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON property: ' + key)
        result[key] = value
    return result


def parse(data):
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('Nonfinite JSON number')
        return number
    return json.loads(data, object_pairs_hook=unique_pairs,
                      parse_float=finite_float,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def fields(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('Missing or unknown contract properties')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', value):
        raise ValueError('Identifiers must be lowercase portable names (1-64 characters)')


def collection(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ValueError('Expected between 1 and 100 items')


def validate(config):
    fields(config, ('contract_version', 'campaign_id', 'scenarios'))
    if type(config['contract_version']) is not int or config['contract_version'] != 1:
        raise ValueError('Unsupported campaign contract version')
    identifier(config['campaign_id'])
    collection(config['scenarios'])
    ids = set()
    for scenario in config['scenarios']:
        fields(scenario, ('id', 'fixture', 'required', 'checks'), ('disposition', 'reason'))
        identifier(scenario['id'])
        if scenario['id'] in ids:
            raise ValueError('Duplicate scenario identity')
        ids.add(scenario['id'])
        relative(scenario['fixture'])
        local(FIXTURES, scenario['fixture'])
        if type(scenario['required']) is not bool:
            raise ValueError('required must be boolean')
        disposition = scenario.get('disposition', 'run')
        if disposition not in ('run', 'skip', 'not_run'):
            raise ValueError('Unknown disposition')
        if disposition != 'run':
            if not isinstance(scenario.get('reason'), str) or not scenario['reason'].strip():
                raise ValueError('Excluded scenarios require a reason')
        elif 'reason' in scenario:
            raise ValueError('reason is only valid for excluded scenarios')
        collection(scenario['checks'])
        checks = set()
        for check in scenario['checks']:
            fields(check, ('id', 'kind', 'required'), ('path', 'expected'))
            identifier(check['id'])
            if check['id'] in checks:
                raise ValueError('Duplicate check identity')
            checks.add(check['id'])
            kind = check['kind']
            if not isinstance(kind, str) or kind not in KINDS or type(check['required']) is not bool:
                raise ValueError('Invalid check kind or requiredness')
            needs_path = kind in ('exists', 'absent', 'text', 'json')
            needs_expected = kind in ('text', 'json', 'tree')
            if ('path' in check) != needs_path or ('expected' in check) != needs_expected:
                raise ValueError('Check properties do not match oracle kind')
            if needs_path:
                relative(check['path'])
            if kind == 'text' and not isinstance(check['expected'], str):
                raise ValueError('Text oracle requires an expected string')
            if kind == 'tree':
                expected = check['expected']
                if not isinstance(expected, list) or any(not isinstance(p, str) for p in expected):
                    raise ValueError('Tree oracle requires a list of file paths')
                for path in expected:
                    relative(path)
                if len(set(expected)) != len(expected):
                    raise ValueError('Duplicate expected path')
    return config
