"""Conservative local path and bounded evidence operations."""
import hashlib
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'fixtures'
RESULTS = ROOT.parent / 'codex-workspace-test-harness-results'
MAX_FILE = 1024 * 1024
MAX_TOTAL = 8 * MAX_FILE
MAX_FILES = 1000


class BoundaryError(ValueError):
    """A path or evidence bound was violated."""


def no_links(path):
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise BoundaryError('Links and reparse points are not allowed')
    return path


def contained(path, root):
    path, root = no_links(path), no_links(root)
    if not path.is_relative_to(root):
        raise BoundaryError('Path escapes authorized root')
    return path


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise BoundaryError('Expected a nonempty portable relative path')
    parts = value.split('/')
    if any(p in ('', '.', '..') or not re.fullmatch(r'[A-Za-z0-9_.-]+', p)
           or p.endswith(('.', ' ')) or p.split('.')[0].upper() in
           {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(1, 10)),
            *('LPT'+str(i) for i in range(1, 10))} for p in parts):
        raise BoundaryError('Unsafe relative path')
    return value


def local(root, value):
    return contained(Path(root) / relative(value), root)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    no_links(path)
    if not path.is_file() or path.stat().st_size > MAX_FILE:
        raise BoundaryError('Evidence is not a bounded regular file')
    with path.open('rb') as stream:
        data = stream.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise BoundaryError('Evidence exceeds file limit')
    return data


def snapshot(root):
    no_links(root)
    if not root.is_dir():
        raise FileNotFoundError('Fixture directory is unavailable')
    files, directories, size = {}, [], 0
    def unavailable(error):
        raise error
    for parent, dirs, names in os.walk(root, followlinks=False, onerror=unavailable):
        for name in sorted(dirs + names):
            path = contained(Path(parent) / name, root)
            key = path.relative_to(root).as_posix()
            relative(key)
            if path.is_dir():
                directories.append(key)
            else:
                data = read(path)
                size += len(data)
                files[key] = sha(data)
            if len(files) + len(directories) > MAX_FILES or size > MAX_TOTAL:
                raise BoundaryError('Fixture exceeds tree limits')
    return {'files': dict(sorted(files.items())), 'directories': sorted(directories)}


def tree_digest(tree):
    import json
    return sha(json.dumps(tree, sort_keys=True, separators=(',', ':')).encode())
