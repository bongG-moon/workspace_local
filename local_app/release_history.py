"""Lazy, anonymous release notes from the application's fixed update source.

Reading a snapshot never starts a request. The separate cache contains only
validated public notes and is never used to select or install an update.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import re
import threading
import time

from .app_updates import CHECK_DEADLINE, RELEASE_ROOT, source_download, version_tuple
from .history import HistoryStore, safe
from .update_source import GITHUB_SOURCE, UpdateSource


MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 30
MAX_TITLE = 300
MAX_NOTES = 32768
CACHE_TTL = 5 * 60
FORCE_INTERVAL = 60
NOTE_SOURCES = frozenset(('deployment', 'gitlab-release', 'package'))
HISTORY_ERROR = '업데이트 기록을 확인하지 못했습니다. 저장된 기록을 표시합니다. 잠시 후 다시 확인해 주세요.'
_STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|\+00:00)\Z')


def _invalid():
    raise ValueError(HISTORY_ERROR)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _decode(data):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        _invalid()
    return json.loads(data.decode('utf-8-sig'), object_pairs_hook=_pairs)


def _read(path):
    with safe(path).open('rb') as stream:
        before = os.fstat(stream.fileno())
        if not 0 < before.st_size <= MAX_BYTES:
            _invalid()
        data = stream.read(before.st_size + 1)
        after = os.fstat(stream.fileno())
        if (len(data) != before.st_size or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns):
            _invalid()
    return _decode(data)


def _text(value, limit):
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(c) < 32 and c not in '\n\r\t' or ord(c) == 127 for c in value)):
        _invalid()
    value.encode('utf-8')  # Reject lone JSON surrogate escapes before caching.
    return value


def _timestamp(value):
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        _invalid()
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() != timedelta(0):
        _invalid()
    return value


def _entry(version, title, notes, published=None, *, provider=None, note_source='deployment'):
    version_tuple(version)
    entry = {'version': version, 'title': _text(title, MAX_TITLE), 'notes': _text(notes, MAX_NOTES),
            'publishedAt': _timestamp(published) if provider else None,
            'origin': 'published' if provider else 'bundled',
            'url': f'{RELEASE_ROOT}/tag/v{version}' if provider == 'github' else ''}
    if provider:
        if (not isinstance(note_source, str) or note_source not in NOTE_SOURCES
                or provider != 'gitlab' and note_source != 'deployment'):
            _invalid()
        if note_source == 'package' and (notes != '' or title != f'AX Workspace {version}'):
            _invalid()
        entry['noteSource'] = note_source
    return entry


def _ordered(entries):
    versions = [entry['version'] for entry in entries]
    if len(versions) != len(set(versions)):
        _invalid()
    return sorted(entries, key=lambda entry: version_tuple(entry['version']), reverse=True)


def parse_gitlab(value):
    if (not isinstance(value, dict) or set(value) != {'schema', 'releases'}
            or type(value.get('schema')) is not int or value['schema'] not in (1, 2)
            or not isinstance(value['releases'], list) or len(value['releases']) > MAX_ENTRIES):
        _invalid()
    entries = []
    fields = {'version', 'title', 'notes', 'publishedAt'}
    if value['schema'] == 2:
        fields.add('noteSource')
    for item in value['releases']:
        if not isinstance(item, dict) or set(item) != fields:
            _invalid()
        entries.append(_entry(item['version'], item['title'], item['notes'], item['publishedAt'],
                              provider='gitlab', note_source=item.get('noteSource', 'deployment')))
    return _ordered(entries)


def parse_github(value):
    if not isinstance(value, list) or len(value) > MAX_ENTRIES:
        _invalid()
    entries = []
    for item in value:
        if not isinstance(item, dict):
            _invalid()
        if item.get('draft') is not False or item.get('prerelease') is not False or item.get('published_at') is None:
            continue
        tag = item.get('tag_name')
        if not isinstance(tag, str) or not tag.startswith('v'):
            continue
        try:
            version_tuple(tag[1:])
        except ValueError:
            continue  # Ignore preview/non-version tags from the native Releases list.
        if item.get('html_url') != f'{RELEASE_ROOT}/tag/{tag}':
            _invalid()
        title, notes = item.get('name'), item.get('body')
        entries.append(_entry(tag[1:], tag if title is None or title == '' else title, '' if notes is None else notes,
                              item['published_at'], provider='github'))
    return _ordered(entries)


def parse_bundled(value, current_version):
    if (not isinstance(value, dict) or set(value) != {'schema', 'currentVersion', 'releases'}
            or type(value.get('schema')) is not int or value['schema'] != 1
            or not isinstance(value['releases'], list) or len(value['releases']) > MAX_ENTRIES):
        _invalid()
    # A mismatched/stale bundle cannot claim a version newer than this build.
    ceiling = min(version_tuple(current_version), version_tuple(value['currentVersion']))
    entries = []
    for item in value['releases']:
        if not isinstance(item, dict) or set(item) != {'version', 'title', 'notes'}:
            _invalid()
        entries.append(_entry(item['version'], item['title'], item['notes']))
    return [entry for entry in _ordered(entries) if version_tuple(entry['version']) <= ceiling]


class HistoryManager:
    def __init__(self, state, current_version, *, bundle_root, source=None, demo=False,
                 transport=None, clock=time.time):
        version_tuple(current_version)
        self.current_version = current_version
        self.source = GITHUB_SOURCE if source is None else source
        if not isinstance(self.source, UpdateSource):
            _invalid()
        self.path = Path(state) / 'release-history.json'
        self.demo, self.clock = bool(demo), clock
        self.transport = transport or (lambda url, **kwargs: source_download(self.source, url, **kwargs))
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread = None
        self._status, self._error = 'idle', None
        self._checked = None
        self._bundled, self._published = [], []
        try:
            self._bundled = parse_bundled(_read(Path(bundle_root) / 'deploy' / 'workspace-release-notes.json'), current_version)
        except (OSError, ValueError, TypeError, RecursionError):
            pass  # A missing offline catalog must not prevent the app from opening.
        if not self.source.error:
            self._load()
        else:
            self._status, self._error = 'error', self.source.error

    def _load(self):
        try:
            if not safe(self.path).exists():
                return
            value = _read(self.path)
            if (not isinstance(value, dict)
                    or set(value) != {'schema', 'sourceIdentity', 'checkedAt', 'entries'}
                    or type(value['schema']) is not int or value['schema'] not in (1, 2)):
                _invalid()
            if value['sourceIdentity'] != self.source.identity:
                return
            checked = value['checkedAt']
            if (type(checked) not in (int, float) or not math.isfinite(checked)
                    or checked < 0 or checked > self.clock() + 300):
                _invalid()
            items = value['entries']
            if not isinstance(items, list) or len(items) > MAX_ENTRIES:
                _invalid()
            entries = []
            fields = {'version', 'title', 'notes', 'publishedAt', 'origin', 'url'}
            if value['schema'] == 2:
                fields.add('noteSource')
            for item in items:
                if not isinstance(item, dict) or set(item) != fields:
                    _invalid()
                entry = _entry(item['version'], item['title'], item['notes'], item['publishedAt'],
                               provider=self.source.provider, note_source=item.get('noteSource', 'deployment'))
                normalized = {**item, 'noteSource': item.get('noteSource', 'deployment')}
                if normalized != entry:
                    _invalid()
                entries.append(entry)
            self._published, self._checked, self._status = _ordered(entries), checked, 'ready'
        except (OSError, ValueError, TypeError, RecursionError):
            self._status, self._error = 'error', HISTORY_ERROR

    def _persist(self):
        payload = {'schema': 2, 'sourceIdentity': self.source.identity,
                   'checkedAt': self._checked, 'entries': self._published}
        data = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(data) > MAX_BYTES:
            return
        try:
            HistoryStore(self.path.parent)._write(self.path, data)
        except (OSError, ValueError):
            pass  # Memory remains usable when the optional disk cache is unwritable.

    def _snapshot(self):
        merged = {entry['version']: entry for entry in self._bundled}
        merged.update({entry['version']: entry for entry in self._published})
        entries = sorted(merged.values(), key=lambda entry: version_tuple(entry['version']), reverse=True)[:MAX_ENTRIES]
        return {'status': self._status, 'currentVersion': self.current_version, 'source': self.source.public(),
                'entries': [dict(entry) for entry in entries], 'checkedAt': self._checked, 'error': self._error}

    def snapshot(self):
        with self._lock:
            return self._snapshot()

    def check(self, *, force=False):
        if type(force) is not bool:
            _invalid()
        with self._lock:
            if self.demo or self.source.error or self._cancel.is_set() or self._status == 'loading':
                return self._snapshot()
            now = self.clock()
            interval = FORCE_INTERVAL if force else CACHE_TTL
            if self._checked is not None and now - self._checked < interval:
                return self._snapshot()
            self._checked, self._status, self._error = now, 'loading', None
            self._thread = threading.Thread(target=self._fetch, name='workspace-release-history', daemon=True)
            try:
                self._thread.start()
            except RuntimeError:
                self._status, self._error = 'error', HISTORY_ERROR
            return self._snapshot()

    def _fetch(self):
        deadline = time.monotonic() + CHECK_DEADLINE
        try:
            raw = self.transport(self.source.history_url, limit=MAX_BYTES, deadline=deadline, cancel=self._cancel)
            if self._cancel.is_set() or time.monotonic() >= deadline:
                _invalid()
            payload = _decode(raw)
            entries = parse_gitlab(payload) if self.source.provider == 'gitlab' else parse_github(payload)
            with self._lock:
                if self._cancel.is_set():
                    return
                self._published, self._status, self._error = entries, 'ready', None
                self._persist()
        except Exception:
            with self._lock:
                if not self._cancel.is_set():
                    self._status, self._error = 'error', HISTORY_ERROR

    def close(self):
        # The bounded HTTP worker is a daemon. Never delay app shutdown for a
        # socket wait; its cancel checks also prevent late cache/state writes.
        with self._lock:
            self._cancel.set()
            if self._status == 'loading':
                self._status = 'ready' if self._published else 'idle'
                self._error = None
        return True
