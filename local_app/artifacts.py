"""Bounded observations of document metadata, not claims of agent authorship."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import stat
import time

from .windows_paths import redirects_path, workspace_path
from .file_preview import ARCHIVE_TYPES, CODE_LANGUAGES, private_name

DOCUMENT_TYPES = {'.md', '.txt', '.csv', '.tsv', '.html', '.htm', '.pdf', '.pptx', '.docx', '.xlsx', '.png', '.jpg', '.jpeg', '.webp'}
# Archives are listed by metadata only and offer an external-open handoff.
# Source previews remain separate from the external-app opening allowlist.
PREVIEW_TYPES = DOCUMENT_TYPES | ARCHIVE_TYPES | set(CODE_LANGUAGES)
# Referencing a file is separate from parsing or opening it in an external app.
# Binary references are metadata/path attachments: never execute or extract them
# during discovery, selection or upload.
ARCHIVE_FILE_TYPES = frozenset({
    '.zip', '.7z', '.rar', '.tar', '.gz', '.tgz', '.bz2', '.tbz', '.tbz2',
    '.xz', '.txz', '.zst', '.tzst', '.lz', '.lzma', '.lz4', '.cab', '.alz', '.egg',
})
REFERENCE_FILE_TYPES = frozenset(DOCUMENT_TYPES) | frozenset({
    '.py', '.pyi', '.pyw', '.js', '.mjs', '.cjs', '.ts', '.mts', '.cts',
    '.tsx', '.jsx', '.css', '.scss', '.sass', '.less', '.json', '.jsonc',
    '.yaml', '.yml', '.toml', '.xml', '.sql', '.sh', '.bash', '.zsh',
    '.ps1', '.psm1', '.psd1', '.bat', '.cmd', '.cs', '.java', '.go',
    '.rs', '.c', '.h', '.cc', '.cpp', '.cxx', '.hpp', '.hxx', '.ini',
    '.cfg', '.conf', '.properties', '.log', '.ipynb', '.r', '.rmd',
    '.rb', '.php', '.vue', '.svelte', '.swift', '.kt', '.kts', '.dart',
    '.lua', '.pl', '.ex', '.exs', '.fs', '.fsx', '.vb', '.vbs', '.tex',
    '.rst', '.adoc', '.graphql', '.gql', '.proto', '.exe', '.markdown',
}) | ARCHIVE_FILE_TYPES

EXCLUDED = {'node_modules', 'venv', '__pycache__', 'build', 'dist'}


@dataclass
class Snapshot:
    files: dict = field(default_factory=dict)
    complete_directories: set = field(default_factory=set)
    limited: bool = False
    errors: int = 0
    scanned: int = 0

    def public(self):
        return {'limited': self.limited, 'errors': self.errors, 'scanned': self.scanned,
                'scope': 'workspace-top-two-levels'}


def linked(path):
    info = path.lstat()
    return redirects_path(info)


def snapshot(root: Path, *, max_files=200, max_entries=2000, max_directories=30, seconds=.5):
    """Read stat data only, without descending into links or unknown depth.

    A directory counts as complete only after its iterator is exhausted. This
    prevents a file omitted by a scan limit from later being called newly made.
    """
    result = Snapshot()
    deadline = time.monotonic() + seconds
    try:
        if root.resolve(strict=True) != root or any(linked(p) for p in (root, *root.parents)):
            result.errors += 1
            return result
    except (OSError, ValueError):
        result.errors += 1
        return result
    pending = [(root, 0)]
    entries = 0
    for directory, depth in pending:
        if time.monotonic() >= deadline:
            result.limited = True
            break
        try:
            if linked(directory):
                continue
            with os.scandir(directory) as children:
                for child in children:
                    entries += 1
                    if entries > max_entries or len(result.files) >= max_files or time.monotonic() >= deadline:
                        result.limited = True
                        return result
                    if private_name(child.name) or child.name in EXCLUDED:
                        continue
                    try:
                        info = child.stat(follow_symlinks=False)
                        if redirects_path(info):
                            continue
                        path = Path(child.path)
                        if stat.S_ISDIR(info.st_mode) and depth == 0:
                            if len(pending) < max_directories + 1:
                                pending.append((path, 1))
                            else:
                                result.limited = True
                        elif stat.S_ISREG(info.st_mode) and path.suffix.lower() in REFERENCE_FILE_TYPES:
                            if path.resolve(strict=True).parent != directory:
                                continue
                            result.files[str(path)] = (info.st_mtime_ns, info.st_size)
                            result.scanned += 1
                    except (OSError, ValueError):
                        result.errors += 1
                # Errors make absence uncertain, so do not assert creations.
                if result.errors == 0:
                    result.complete_directories.add(str(directory))
        except OSError:
            result.errors += 1
    return result


def list_materials(root: Path, directory='', *, max_items=200, max_entries=2000, seconds=.5):
    """List only the requested folder's metadata; never scan descendants."""
    if (not isinstance(directory, str) or len(directory) > 32767
            or '\x00' in directory or Path(directory).is_absolute()
            or Path(directory).drive or '..' in Path(directory).parts):
        raise ValueError('업무 폴더 안의 자료 위치를 선택해 주세요.')
    parts = Path(directory).parts
    if any(private_name(part) for part in parts):
        raise ValueError('숨김 설정 또는 인증 정보로 보이는 폴더는 자료 목록에서 열 수 없습니다.')
    root = workspace_path(root)
    target = workspace_path(root.joinpath(*parts))
    if not target.is_relative_to(root) or not target.is_dir():
        raise ValueError('자료 폴더가 없거나 위치가 바뀌었어요. 업무 폴더로 돌아가 주세요.')
    deadline = time.monotonic() + seconds
    rows, scanned, limited, errors = [], 0, False, 0
    with os.scandir(target) as children:
        for child in children:
            if scanned >= max_entries or len(rows) >= max_items or time.monotonic() >= deadline:
                limited = True
                break
            scanned += 1
            if private_name(child.name):
                continue
            try:
                info = child.stat(follow_symlinks=False)
                if redirects_path(info):
                    continue
                is_directory = stat.S_ISDIR(info.st_mode)
                path = Path(child.path)
                if not is_directory and (not stat.S_ISREG(info.st_mode) or path.suffix.lower() not in REFERENCE_FILE_TYPES):
                    continue
                rows.append({'name': child.name, 'path': str(path),
                             'relativePath': path.relative_to(root).as_posix(),
                             'kind': 'directory' if is_directory else 'file',
                             'size': 0 if is_directory else info.st_size})
            except OSError:
                errors += 1
    rows.sort(key=lambda row: (row['kind'] != 'directory', row['name'].casefold(), row['name']))
    return {'files': rows, 'directory': '' if target == root else target.relative_to(root).as_posix(),
            'limited': limited, 'errors': errors}


def changes(root: Path, before: Snapshot, after: Snapshot, run_id: str, observed_at: float):
    rows = []
    for value, signature in after.files.items():
        path = Path(value)
        previous = before.files.get(value)
        if previous == signature:
            continue
        if previous is None and str(path.parent) not in before.complete_directories:
            # A new child folder can be observed safely when the root scan was
            # complete: none of that folder's documents existed at the start.
            if not (str(root) in before.complete_directories and path.parent.parent == root
                    and str(path.parent) not in before.complete_directories
                    and not before.limited and not before.errors):
                continue
        rows.append({'path': value, 'name': str(path.relative_to(root)),
                     'change': 'created' if previous is None else 'modified',
                     'runId': run_id, 'observedAt': observed_at, 'size': signature[1]})
    return rows


def available_artifacts(root: Path, rows, *, seconds=.25):
    """Filter a bounded display snapshot, without changing historical evidence.

    Read metadata only. A missing path is hidden; permission/I/O errors and a
    spent time budget remain unknown and keep the previous row. Check ancestry
    before children so replaced directories cannot redirect the lookup.
    """
    rows = list(rows)
    result = {'missing': 0, 'errors': 0, 'limited': False}
    if not rows:
        return rows, result
    deadline = time.monotonic() + seconds
    cache = {}

    def kind(path):
        if path in cache:
            return cache[path]
        if time.monotonic() >= deadline:
            result['limited'] = True
            return None
        try:
            info = path.lstat()
            value = ('blocked' if redirects_path(info) else 'directory' if stat.S_ISDIR(info.st_mode)
                     else 'file' if stat.S_ISREG(info.st_mode) else 'blocked')
        except (FileNotFoundError, NotADirectoryError):
            value = 'missing'
        except OSError:
            result['errors'] += 1
            value = None
        cache[path] = value
        return value

    # An unavailable workspace is not evidence that all its files were deleted.
    # Preserve its rows for a later refresh instead of erasing the display.
    if not root.is_absolute() or '..' in root.parts or '\x00' in str(root):
        result['errors'] += 1
        return rows, result
    for parent in reversed((root, *root.parents)):
        root_kind = kind(parent)
        if root_kind != 'directory':
            if root_kind is not None:
                result['errors'] += 1
            return rows, result

    visible = []
    for row in rows:
        path = Path(row['path'])
        if (not path.is_absolute() or not path.is_relative_to(root)
                or '..' in path.parts or '\x00' in str(path)):
            result['missing'] += 1
            continue
        relative = path.relative_to(root)
        if not relative.parts or any(private_name(part) for part in relative.parts):
            result['missing'] += 1
            continue
        current, available = root, True
        for index, part in enumerate(relative.parts):
            current = current / part
            actual = kind(current)
            if actual is None:
                break  # Unknown: keep this row, not a confirmed deletion.
            expected = 'file' if index == len(relative.parts) - 1 else 'directory'
            if actual != expected:
                available = False
                break
        if available:
            visible.append(row)
        else:
            result['missing'] += 1
    return visible, result
