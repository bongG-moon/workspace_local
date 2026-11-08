"""Bounded, transactional unsent drafts. No polling, background thread or replay.

Only changed rows are written. Empty rows retain a revision so delayed writes
cannot resurrect a sent/cleared draft. The CLI's transcript is never touched.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
import uuid

from .history import safe

MAX_TOTAL = 4 * 1024 * 1024
MAX_ROWS = 12000
MAX_REQUEST = 1024 * 1024
TABLE_MARKER = re.compile(r'^〔표 인용 ([1-9][0-9]{0,15})〕 ', re.MULTILINE)


class StaleTableReference(ValueError):
    """An explicitly quoted row must be selected again before delivery."""

    code = 'stale_table_reference'

    def __init__(self, message, reference_id=None, *, can_review=False):
        super().__init__(message)
        self.reference_id = reference_id
        self.can_review = can_review


def identifier(value):
    if value in ('home', 'home:chat', 'home:work'):
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

    @contextmanager
    def _table_db(self):
        try:
            with self.lock, self._connect() as db:
                yield db
        except sqlite3.Error as exc:
            raise ValueError('표 인용 기록을 확인하지 못했어요. 입력한 내용은 유지됩니다.') from exc

    @staticmethod
    def _ensure_table_sources(db):
        fields = {row[1] for row in db.execute('PRAGMA table_info(table_sources)')}
        if 'scope' in fields:
            return
        if fields:
            # The old UNIQUE index covered a whole row. Retain its receipt IDs,
            # including the high-water mark of receipts already removed.
            previous_seq = db.execute("SELECT seq FROM sqlite_sequence WHERE name='table_sources'").fetchone()
            db.execute('ALTER TABLE table_sources RENAME TO old_table_sources')
        db.execute('CREATE TABLE table_sources ('
                   'id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, path TEXT NOT NULL, '
                   "version TEXT NOT NULL, row_number INTEGER NOT NULL, scope TEXT NOT NULL DEFAULT '', "
                   'UNIQUE(owner,path,version,row_number,scope))')
        if fields:
            db.execute('INSERT INTO table_sources(id,owner,path,version,row_number) '
                       'SELECT id,owner,path,version,row_number FROM old_table_sources')
            if previous_seq:
                db.execute("DELETE FROM sqlite_sequence WHERE name='table_sources'")
                db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('table_sources',?)", previous_seq)
            db.execute('DROP TABLE old_table_sources')

    def record_table_source(self, sid, path, version, row, *, scope=None, replaces=None):
        """Persist coordinates and the source hash; cell contents stay with their source."""
        identifier(sid)
        scope_key = json.dumps(scope, separators=(',', ':')) if scope is not None else ''
        with self._table_db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._ensure_table_sources(db)
            if replaces is not None and not db.execute(
                    'SELECT 1 FROM table_sources WHERE owner=? AND id=? AND path=?',
                    (sid, replaces, path)).fetchone():
                raise StaleTableReference('이 업무에서 교체할 표 인용을 확인할 수 없어요.', replaces)
            previous = db.execute('SELECT id FROM table_sources WHERE owner=? AND path=? AND version=? AND row_number=? AND scope=?',
                                  (sid, path, version, row, scope_key)).fetchone()
            if previous:
                return previous[0]
            number = db.execute('INSERT INTO table_sources(owner,path,version,row_number,scope) VALUES(?,?,?,?,?)',
                                (sid, path, version, row, scope_key)).lastrowid
            db.execute('DELETE FROM table_sources WHERE id <= ?', (number - 4096,))
            return number

    def issued_table_source(self, sid, number):
        identifier(sid)
        if type(number) is not int or not 1 <= number < 10**16:
            raise ValueError('교체할 표 인용 번호를 확인해 주세요.')
        with self._table_db() as db:
            fields = {row[1] for row in db.execute('PRAGMA table_info(table_sources)')}
            scope = 'scope' if 'scope' in fields else "''"
            row = db.execute(f'SELECT path,version,row_number,{scope} FROM table_sources WHERE owner=? AND id=?',
                             (sid, number)).fetchone() if fields else None
        if not row:
            raise StaleTableReference('이 업무에서 확인할 수 없는 표 인용이에요. 원본 표에서 행을 다시 선택해 주세요.', number)
        return (*row[:3], json.loads(row[3]) if row[3] else None)

    def table_sources(self, sid, text):
        if not isinstance(text, str) or '〔표 인용 ' not in text:
            return []
        numbers = list(dict.fromkeys(map(int, TABLE_MARKER.findall(text))))
        if not numbers:
            return []
        if len(numbers) > 8:
            raise ValueError('한 번에 표의 행을 8개까지 인용할 수 있어요.')
        with self._table_db() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='table_sources' AND type='table'").fetchone()
            slots = ','.join('?' for _ in numbers)
            rows = db.execute(f'SELECT id,path,version FROM table_sources WHERE owner=? AND id IN ({slots})',
                              (sid, *numbers)).fetchall() if exists else []
        if len(rows) != len(numbers):
            found = {row[0] for row in rows}
            missing = next(number for number in numbers if number not in found)
            raise StaleTableReference('이 업무에서 확인할 수 없는 표 인용이 있어요. 원본 표에서 행을 다시 선택해 주세요.', missing)
        return rows

    def delete_session(self, sid):
        identifier(sid)
        with self.lock:
            if not safe(self.path).exists():
                return
            with self._connect() as db:
                db.execute('DELETE FROM drafts WHERE id=?', (sid,))
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='table_sources' AND type='table'").fetchone():
                    db.execute('DELETE FROM table_sources WHERE owner=?', (sid,))
                db.execute("UPDATE metadata SET value='home:chat' WHERE key='selected' AND value=?", (sid,))
