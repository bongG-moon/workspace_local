"""Bounded, transactional unsent drafts. No polling, background thread or replay.

Only changed rows are written. Empty rows retain a revision so delayed writes
cannot resurrect a sent/cleared draft. The CLI's transcript is never touched.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
import uuid

from .history import safe

MAX_TOTAL = 4 * 1024 * 1024
MAX_ROWS = 12000
MAX_REQUEST = 1024 * 1024


def identifier(value):
    if value == 'home':
        return value
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('초안을 저장할 업무를 확인해 주세요.')
    return value


def content(value, stash=False):
    if value is None:
        return None
    keys = {'text', 'attachments'} | ({'selectionStart', 'selectionEnd'} if stash else set())
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('초안의 저장 형식을 확인해 주세요.')
    text, paths = value['text'], value['attachments']
    if not isinstance(text, str) or len(text) > 100000:
        raise ValueError('초안은 10만 자까지 자동 저장할 수 있어요.')
    if not isinstance(paths, list) or len(paths) > 12 or any(
            not isinstance(p, str) or not p or len(p) > 8192 or '\0' in p for p in paths):
        raise ValueError('초안의 첨부 자료를 확인해 주세요.')
    try:
        text.encode('utf-8')
    except UnicodeError as exc:
        raise ValueError('초안의 문자를 확인해 주세요.') from exc
    clean = {'text': text, 'attachments': list(paths)}
    if stash:
        start, end = value['selectionStart'], value['selectionEnd']
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(text.encode('utf-16-le')) // 2:
            raise ValueError('보관한 입력의 선택 범위를 확인해 주세요.')
        clean.update(selectionStart=start, selectionEnd=end)
    return clean


class DraftConflict(ValueError):
    code = 'draft_conflict'


class DraftStore:
    def __init__(self, state):
        self.path = Path(state) / 'drafts.sqlite3'
        self.lock = threading.RLock()
        self.warning = None

    @contextmanager
    def _connect(self):
        safe(self.path)
        safe(self.path.with_name(self.path.name + '-journal'))
        db = sqlite3.connect(self.path, timeout=2)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL, bytes INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)')
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _body(raw):
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {'draft', 'stash'}:
            raise ValueError('초안 저장 형식을 확인하지 못했습니다.')
        return {'draft': content(value['draft']), 'stash': content(value['stash'], stash=True)}

    def snapshot(self):
        with self.lock:
            if not safe(self.path).exists():
                return {'entries': [], 'selectedId': 'home'}
            try:
                with self._connect() as db:
                    count, total = db.execute('SELECT COUNT(*), COALESCE(SUM(bytes),0) FROM drafts').fetchone()
                    if count > MAX_ROWS or total > MAX_TOTAL:
                        raise ValueError('초안 저장 범위를 초과했습니다.')
                    rows = [{'id': identifier(sid), 'revision': rev, **self._body(raw)}
                            for sid, rev, raw in db.execute('SELECT id, revision, body FROM drafts')]
                    selected = db.execute("SELECT value FROM metadata WHERE key='selected'").fetchone()
                self.warning = None
                return {'entries': rows, 'selectedId': identifier(selected[0]) if selected else 'home'}
            except (sqlite3.Error, ValueError, OSError) as exc:
                self.warning = '저장된 초안을 읽지 못했어요. 기존 파일을 보존했습니다. 다시 실행하기 전에 현재 입력을 따로 보관해 주세요.'
                raise ValueError(self.warning) from exc

    def update(self, data):
        if not isinstance(data, dict) or set(data) != {'id', 'revision', 'draft', 'stash', 'selectedId'}:
            raise ValueError('초안 저장 요청을 확인해 주세요.')
        sid, selected = identifier(data['id']), identifier(data['selectedId'])
        revision = data['revision']
        if type(revision) is not int or revision < 0:
            raise ValueError('초안의 저장 순서를 확인해 주세요.')
        body = {'draft': content(data['draft']), 'stash': content(data['stash'], stash=True)}
        raw = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
        size = len(raw.encode('utf-8'))
        with self.lock:
            if self.warning:
                raise ValueError(self.warning)
            try:
                with self._connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    previous = db.execute('SELECT revision, body, bytes FROM drafts WHERE id=?', (sid,)).fetchone()
                    current = previous[0] if previous else 0
                    # A lost HTTP response may retry the exact committed row.
                    if current != revision:
                        if previous and current == revision + 1 and previous[1] == raw:
                            return {'id': sid, 'revision': current}
                        raise DraftConflict('다른 창에서 초안이 변경됐어요. 이 창의 입력을 복사해 보관한 뒤 화면을 다시 열어 주세요.')
                    if previous and previous[1] == raw:
                        result = current
                    else:
                        count, total = db.execute('SELECT COUNT(*), COALESCE(SUM(bytes),0) FROM drafts').fetchone()
                        if (not previous and count >= MAX_ROWS) or total - (previous[2] if previous else 0) + size > MAX_TOTAL:
                            raise ValueError('초안 보관 공간이 찼어요. 필요 없는 초안을 비우고 다시 저장해 주세요.')
                        result = current + 1
                        db.execute('INSERT OR REPLACE INTO drafts VALUES (?,?,?,?)', (sid, result, raw, size))
                    db.execute("INSERT OR REPLACE INTO metadata VALUES ('selected',?)", (selected,))
                return {'id': sid, 'revision': result}
            except sqlite3.Error as exc:
                raise ValueError('초안을 저장하지 못했어요. 입력은 화면에 유지됩니다. 저장 위치와 여유 공간을 확인해 주세요.') from exc

    def reference_paths(self):
        # Called only by explicit storage management, never a keystroke/heartbeat.
        return {path for row in self.snapshot()['entries'] for kind in ('draft', 'stash')
                for path in (row.get(kind) or {}).get('attachments', [])}
