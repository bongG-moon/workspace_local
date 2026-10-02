"""Sanitized CLI evidence, kept off the conversation/event payloads.

The separate per-session SQLite log has bounded disk usage and indexed paging.
Only explicitly supported public CLI blocks are projected; wire frames, private
thinking, authentication and the control protocol are never stored.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing, nullcontext
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from .executions import safe_run_id
from .history import safe

MAX_RECORD_BYTES = 64 * 1024
MAX_SESSION_BYTES = 32 * 1024 * 1024
MAX_PAGE = 100
KINDS = {'assistant', 'tool_input', 'tool_result', 'tool_progress', 'task', 'hook', 'status', 'result', 'error', 'notice'}
_ID = re.compile(r'[A-Za-z0-9_:.\-/]{1,160}\Z')
_TOOL = re.compile(r'[A-Za-z][A-Za-z0-9_.:/-]{0,159}\Z')
_SECRET_KEY = re.compile(r'(?:authorization|cookie|password|passwd|secret|credential|(?:access|refresh|auth|api|private|deploy|session)[_-]?(?:key|token)|api[_-]?key|token)', re.I)
_PRIVATE_KEY = re.compile(r'-----BEGIN (?:[A-Z ]*PRIVATE KEY)-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)', re.S)
_BEARER = re.compile(r'(?i)\b(Bearer|Basic)\s+[A-Za-z0-9+/_.=:-]+')
_KNOWN_TOKEN = re.compile(r'\b(?:sk-ant-|sk-|glpat-|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{8,}')
_HEADER_SECRET = re.compile(r'''(?im)(\b(?:authorization|proxy-authorization|cookie|set-cookie|private-token|deploy-token|job-token|x-api-key)\s*:\s*)[^\r\n"']+''')
_JWT = re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b')
_ASSIGN = re.compile(r'''(?ix)((?:(?:token|password|passwd|secret|credential|authorization|cookie|api[_-]?key)[\w.-]{0,80}["']?)\s*(?:=|:)\s*)(?:"[^"]*(?:"|\Z)|'[^']*(?:'|\Z)|[^\s,;}]+)''')
_FLAG = re.compile(r'''(?ix)(--?(?:token|password|passwd|secret|api-key|access-token|header|H)\s+)(?:"(?:Authorization|PRIVATE-TOKEN|DEPLOY-TOKEN|JOB-TOKEN)[^"\r\n]*"|'(?:Authorization|PRIVATE-TOKEN|DEPLOY-TOKEN|JOB-TOKEN)[^'\r\n]*'|[^\s]+)''')
_URL_USER = re.compile(r'(?i)(https?://)[^\s/@:]+:[^\s/@]+@')
_MEDIA = re.compile(r'data:[^\s,;]{1,100};base64,[A-Za-z0-9+/=\r\n]*', re.I)
_BINARY = re.compile(r'(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{512,}={0,2}(?![A-Za-z0-9+/])')
_EMPTY_NOTICE = '저장된 상세 진행 기록이 없습니다. 이 기능을 사용하기 전의 진행 내용은 복원할 수 없습니다.'
_ERROR_NOTICE = '상세 진행 기록을 저장하거나 읽을 수 없습니다. 대화 처리는 계속됩니다.'


def _identifier(value):
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def clean_text(value, maximum=MAX_RECORD_BYTES):
    """Redact before limiting so a clipped credential cannot escape detection."""
    if not isinstance(value, str):
        return '', False
    value = _PRIVATE_KEY.sub('[비공개 키 숨김]', value)
    value = _MEDIA.sub('[바이너리 데이터 숨김]', value)
    value = _BINARY.sub('[바이너리 데이터 숨김]', value)
    value = _URL_USER.sub(r'\1[인증 정보 숨김]@', value)
    value = _HEADER_SECRET.sub(r'\1[인증정보숨김]', value)
    value = _BEARER.sub(r'\1 [인증정보숨김]', value)
    value = _ASSIGN.sub(r'\1[인증 정보 숨김]', value)
    value = _FLAG.sub(r'\1[인증 정보 숨김]', value)
    value = _KNOWN_TOKEN.sub('[인증 정보 숨김]', value)
    value = _JWT.sub('[인증 정보 숨김]', value)
    value = ''.join(c for c in value if c in '\n\r\t' or ord(c) >= 32 and ord(c) != 127)
    raw = value.encode('utf-8', errors='replace')
    return raw[:maximum].decode('utf-8', errors='ignore'), len(raw) > maximum


def _clean_value(value, depth=0):
    if depth > 12:
        return '[중첩 내용 생략]'
    if isinstance(value, dict):
        if value.get('type') in {'image', 'document', 'base64', 'thinking', 'redacted_thinking'}:
            return '[비공개 또는 바이너리 내용 숨김]'
        return {str(k)[:200]: ('[인증 정보 숨김]' if _SECRET_KEY.search(str(k)) else
                              '[비공개 내용 숨김]' if str(k).lower() in {'thinking', 'signature', 'base64'} else
                              _clean_value(v, depth + 1)) for k, v in list(value.items())[:1000]}
    if isinstance(value, list):
        return [_clean_value(v, depth + 1) for v in value[:1000]]
    if isinstance(value, str):
        return clean_text(value)[0]
    if value is None or type(value) in (int, float, bool):
        return value
    return ''


def _display_input(value):
    return json.dumps(_clean_value(value), ensure_ascii=False, indent=2, allow_nan=False)


def _result_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(block['text'] for block in value if isinstance(block, dict)
                         and block.get('type') == 'text' and isinstance(block.get('text'), str))
    return ''


class ProgressStore:
    def __init__(self, directory, *, max_bytes=MAX_SESSION_BYTES):
        self.directory = Path(directory)
        self.max_bytes = max(8192, max_bytes)
        self.lock = threading.RLock()
        self.failures = set()

    def _path(self, sid):
        if not isinstance(sid, str) or str(uuid.UUID(sid)) != sid:
            raise ValueError('대화 ID를 확인해 주세요.')
        path = safe(self.directory / (sid + '.sqlite3'))
        if path.exists() and path.stat().st_size > self.max_bytes:
            raise ValueError('상세 진행 기록의 저장 한도를 초과했습니다.')
        # SQLite creates its journal next to the database; never follow links.
        for suffix in ('-journal', '-wal', '-shm'):
            safe(Path(str(path) + suffix))
        return path

    def _connect(self, sid, *, create=False):
        path = self._path(sid)
        if not create and not path.exists():
            return None
        if create:
            safe(self.directory).mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path.as_uri() + ('?mode=rwc' if create else '?mode=ro'), uri=True, timeout=.25)
        try:
            if create:
                db.execute('PRAGMA auto_vacuum=FULL')
                db.execute('PRAGMA journal_mode=DELETE')
                db.execute('PRAGMA cache_size=-256')
                # Reserve space for indexes, headers and the one active journal.
                db.execute('PRAGMA max_page_count=' + str(max(32, self.max_bytes // 4096)))
                db.executescript('''CREATE TABLE IF NOT EXISTS records (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run TEXT NOT NULL,
                    identity TEXT NOT NULL, body TEXT NOT NULL, size INTEGER NOT NULL,
                    UNIQUE(run, identity));
                    CREATE INDEX IF NOT EXISTS records_run ON records(run, seq);
                    CREATE TABLE IF NOT EXISTS metadata (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER,
                    truncated INTEGER, total INTEGER, count INTEGER, last_seq INTEGER);
                    INSERT OR IGNORE INTO metadata VALUES(1, 0, 0, 0, 0, 0);''')
            return db
        except Exception:
            db.close()
            raise

    @staticmethod
    def _metadata(db):
        if db is None:
            return dict(count=0, lastSeq=0, revision=0, truncated=False, available=False)
        revision, cut, _, count, last = db.execute('SELECT revision,truncated,total,count,last_seq FROM metadata WHERE singleton=1').fetchone()
        return dict(count=count, lastSeq=last, revision=revision, truncated=bool(cut), available=True)

    def metadata(self, sid):
        with self.lock:
            try:
                db = self._connect(sid)
                with closing(db) if db is not None else nullcontext():
                    result = self._metadata(db)
                if sid in self.failures:
                    result['truncated'] = True
                return result
            except (OSError, ValueError, sqlite3.Error, TypeError):
                self.failures.add(sid)
                return dict(count=0, lastSeq=0, revision=0, truncated=True, available=False)

    def append(self, sid, value):
        """Insert/update a normalized block. Return metadata only, never content."""
        if (not isinstance(value, dict) or not safe_run_id(value.get('runId'))
                or value.get('kind') not in KINDS or not isinstance(value.get('key'), str)
                or len(value['key']) > 256):
            return None
        text, cut = clean_text(value.get('text'))
        title = clean_text(value.get('title'), 400)[0]
        stamp = value.get('time')
        if type(stamp) not in (float, int) or not math.isfinite(stamp) or not 0 <= stamp < 1e12:
            return None
        row = dict(runId=value['runId'], time=stamp, kind=value['kind'], title=title, text=text)
        for field in ('tool', 'parentToolUseId'):
            if _identifier(value.get(field)):
                row[field] = value[field]
        if cut or value.get('truncated') is True:
            row['truncated'] = True
        body = json.dumps(row, ensure_ascii=False, separators=(',', ':'))
        cost = len(body.encode('utf-8')) + 1024
        with self.lock:
            try:
                with closing(self._connect(sid, create=True)) as db, db:
                    old = db.execute('SELECT seq,body,size FROM records WHERE run=? AND identity=?',
                                     (row['runId'], value['key'])).fetchone()
                    if old and old[1] == body:
                        return None
                    _, cut_log, total, count, last = db.execute('SELECT revision,truncated,total,count,last_seq FROM metadata WHERE singleton=1').fetchone()
                    budget = max(1024, (self.max_bytes - 64 * 1024) * 3 // 4)
                    # Remove oldest evidence before inserting to stay below the
                    # database page cap, including during a large block update.
                    while total - (old[2] if old else 0) + cost > budget and count:
                        oldest = db.execute('SELECT seq,size FROM records ORDER BY seq LIMIT 1').fetchone()
                        db.execute('DELETE FROM records WHERE seq=?', (oldest[0],))
                        total -= oldest[1]
                        count -= 1
                        cut_log = 1
                        if old and old[0] == oldest[0]:
                            old = None
                    if cost > budget:
                        raise ValueError('진행 기록 저장 한도를 초과했습니다.')
                    if old:
                        db.execute('UPDATE records SET body=?,size=? WHERE seq=?', (body, cost, old[0]))
                        total += cost - old[2]
                    else:
                        cursor = db.execute('INSERT INTO records(run,identity,body,size) VALUES(?,?,?,?)',
                                            (row['runId'], value['key'], body, cost))
                        last = cursor.lastrowid
                        count += 1
                        total += cost
                    db.execute('UPDATE metadata SET revision=revision+1,truncated=?,total=?,count=?,last_seq=? WHERE singleton=1',
                               (cut_log, total, count, last))
                    result = self._metadata(db)
                if sid in self.failures:
                    result['truncated'] = True
                return result
            except (OSError, ValueError, sqlite3.Error, TypeError):
                self.failures.add(sid)
                return self.metadata(sid)

    def page(self, sid, *, before=None, limit=50, run_id=None):
        if before is not None and (not str(before).isdigit() or not 0 < int(before) <= 2 ** 63 - 1):
            raise ValueError('진행 기록 위치를 확인해 주세요.')
        if not str(limit).isdigit() or not 1 <= int(limit) <= MAX_PAGE:
            raise ValueError('진행 기록 조회 개수는 1~100 사이여야 합니다.')
        if run_id is not None and not safe_run_id(run_id):
            raise ValueError('요청 ID를 확인해 주세요.')
        with self.lock:
            result = dict(records=[], hasMore=False, nextBefore=None)
            try:
                db = self._connect(sid)
                if db is None:
                    result.update(progress=self._metadata(None), notice=_EMPTY_NOTICE)
                    if sid in self.failures:
                        result['progress']['truncated'] = True
                        result['notice'] = _ERROR_NOTICE
                    return result
                with closing(db):
                    clauses, args = [], []
                    if before is not None:
                        clauses.append('seq < ?')
                        args.append(int(before))
                    if run_id is not None:
                        clauses.append('run = ?')
                        args.append(run_id)
                    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
                    rows = db.execute('SELECT seq,body FROM records' + where + ' ORDER BY seq DESC LIMIT ?', (*args, int(limit) + 1)).fetchall()
                    result['hasMore'] = len(rows) > int(limit)
                    result['records'] = [dict(json.loads(body), seq=seq) for seq, body in reversed(rows[:int(limit)])]
                    result['nextBefore'] = result['records'][0]['seq'] if result['hasMore'] else None
                    result['progress'] = self._metadata(db)
                    if result['progress']['truncated']:
                        result['notice'] = '저장 한도를 넘어 오래된 상세 진행 기록 일부를 지웠습니다.'
                if sid in self.failures:
                    result['progress']['truncated'] = True
                    result['notice'] = _ERROR_NOTICE
                return result
            except (OSError, ValueError, sqlite3.Error, TypeError):
                self.failures.add(sid)
                return dict(records=[], hasMore=False, nextBefore=None, progress=self.metadata(sid), notice=_ERROR_NOTICE)


class ProgressCapture:
    """One submitted root turn. No lifecycle state is inferred from these rows."""
    def __init__(self, emit, *, run_id, clock=time.time, tombstones=None):
        self.emit, self.run_id, self.clock = emit, safe_run_id(run_id), clock
        self.closed = not bool(self.run_id)
        self.tools = OrderedDict()
        self.seen = OrderedDict()
        self.frames = OrderedDict()
        self.root_texts = OrderedDict()
        self.tombstones = tombstones if tombstones is not None else OrderedDict()
        self.message_id = None
        self.blocks = OrderedDict()

    @staticmethod
    def _remember(mapping, key, value=True, maximum=10000):
        mapping[key] = value
        if len(mapping) > maximum:
            mapping.popitem(last=False)

    @staticmethod
    def _digest(value):
        return hashlib.sha256(value.encode('utf-8', errors='replace')).hexdigest()

    def _row(self, key, kind, title, text='', *, parent=None, tool=None, stamp=None, truncated=False):
        clean, cut = clean_text(text)
        row = dict(key=key, runId=self.run_id, kind=kind, title=title, text=clean,
                   time=self.clock() if stamp is None else stamp)
        if parent:
            row['parentToolUseId'] = parent
        if tool:
            row['tool'] = tool
        if cut or truncated:
            row['truncated'] = True
        # Keep only a fingerprint, not a second copy of the detailed transcript.
        fingerprint = self._digest(json.dumps({k: v for k, v in row.items() if k != 'time'}, sort_keys=True))
        if self.seen.get(key) == fingerprint:
            return
        self._remember(self.seen, key, fingerprint)
        try:
            self.emit(row)
        except Exception:
            pass  # A display log must never terminate CLI processing.

    def _message_allowed(self, message_id):
        key = 'message:' + message_id
        owner = self.tombstones.get(key)
        if owner is not None and owner != self.run_id:
            return False
        self._remember(self.tombstones, key, self.run_id, maximum=20000)
        return True

    def _flush(self, block):
        if block['text']:
            self._row(block['key'], 'assistant', 'Claude 메시지', block['text'],
                      stamp=block['time'], truncated=block['truncated'])
        block['last'] = self.clock()

    def finish(self):
        if not self.closed:
            for block in self.blocks.values():
                self._flush(block)
        self.closed = True
        self.blocks.clear()
        self.tools.clear()
        self.frames.clear()
        self.seen.clear()
        self.root_texts.clear()

    def error(self, message):
        if not self.closed and isinstance(message, str):
            self._row('transport-error:' + self._digest(message), 'error', 'CLI 연결 오류', message)

    def handle(self, data):
        if self.closed or not isinstance(data, dict):
            return
        parent = data.get('parent_tool_use_id')
        if parent is not None and (not _identifier(parent) or parent not in self.tools):
            return
        kind = data.get('type')
        if kind not in {'assistant', 'user', 'stream_event', 'tool_progress', 'system', 'result'}:
            return
        if kind == 'assistant' and data.get('error'):
            # Authentication errors can carry credentials/provider response data.
            self._row('assistant-error:' + str(data.get('uuid') or 'reported'), 'error', 'CLI 오류',
                      'Claude가 응답 오류를 보고했습니다. 대화 화면의 오류 안내를 확인해 주세요.', parent=parent)
            return
        frame = _identifier(data.get('uuid'))
        if frame:
            if frame in self.frames or self.tombstones.get('frame:' + frame) not in (None, self.run_id):
                return
            self._remember(self.frames, frame)
            self._remember(self.tombstones, 'frame:' + frame, self.run_id, maximum=20000)
        if kind == 'stream_event':
            if not parent:
                self._stream(data.get('event'))
            return
        if kind == 'assistant':
            message = data.get('message')
            if not isinstance(message, dict):
                return
            message_id = _identifier(message.get('id')) or frame or 'unidentified'
            if not self._message_allowed(message_id):
                return
            blocks = message.get('content')
            for block in blocks if isinstance(blocks, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'text' and isinstance(block.get('text'), str):
                    text = block['text']
                    digest = self._digest(text)
                    key = 'text:' + self._digest(message_id + ':' + str(parent) + ':' + digest)
                    if not parent:
                        self._remember(self.root_texts, digest)
                        if self.message_id == message_id:
                            matches = [v for v in self.blocks.values() if not v['full_seen']
                                       and (v['digest'] == digest if v['complete'] else
                                            text.startswith(v['text']) or v['text'].startswith(text))]
                            if matches:
                                target = matches[0]
                                raw = text.encode('utf-8', errors='replace')
                                target['text'] = raw[:MAX_RECORD_BYTES].decode('utf-8', errors='ignore')
                                target['truncated'] = len(raw) > MAX_RECORD_BYTES
                                target['complete'] = True
                                target['full_seen'] = True
                                target['digest'] = digest
                                self._flush(target)
                                target['text'] = ''
                                self._remember(self.seen, key, 'streamed')
                                continue
                    if key not in self.seen:
                        self._row(key, 'assistant', '작업자 메시지' if parent else 'Claude 메시지', text, parent=parent)
                elif block.get('type') == 'tool_use':
                    tool_id, name = _identifier(block.get('id')), block.get('name')
                    if not tool_id or not isinstance(name, str) or not _TOOL.fullmatch(name):
                        continue
                    tombstone = 'tool:' + tool_id
                    if tool_id in self.tools or self.tombstones.get(tombstone) not in (None, self.run_id):
                        continue
                    self._remember(self.tombstones, tombstone, self.run_id, maximum=20000)
                    self._remember(self.tools, tool_id, (name, parent), maximum=10000)
                    self._row('tool:' + tool_id, 'tool_input', name + ' 호출', _display_input(block.get('input', {})), parent=parent, tool=name)
        elif kind == 'user':
            message = data.get('message')
            blocks = message.get('content') if isinstance(message, dict) else None
            for block in blocks if isinstance(blocks, list) else []:
                if not isinstance(block, dict) or block.get('type') != 'tool_result':
                    continue
                tool_id = _identifier(block.get('tool_use_id'))
                tool = self.tools.get(tool_id)
                if not tool or tool[1] != parent:
                    continue
                text = _result_text(block.get('content'))
                self._row('tool-result:' + tool_id, 'tool_result', tool[0] + (' 오류' if block.get('is_error') is True else ' 결과'), text, parent=parent, tool=tool[0])
        elif kind == 'tool_progress':
            tool_id = _identifier(data.get('tool_use_id'))
            tool = self.tools.get(tool_id)
            elapsed = data.get('elapsed_time_seconds')
            if tool and tool[1] == parent and type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 <= elapsed < 1e9:
                self._row('tool-progress:' + tool_id, 'tool_progress', tool[0] + ' 진행', f'CLI 보고 경과 시간: {elapsed:g}초', parent=parent, tool=tool[0])
        elif kind == 'system':
            self._system(data, parent)
        elif kind == 'result':
            for block in self.blocks.values():
                self._flush(block)
            error = data.get('is_error') is True
            text = data.get('result') if isinstance(data.get('result'), str) else ''
            if error:
                errors = data.get('errors')
                if isinstance(errors, list):
                    text = '\n'.join(x for x in errors if isinstance(x, str)) or text
            elif self._digest(text) in self.root_texts:
                text = ''
            duration = data.get('duration_ms')
            if type(duration) in (int, float) and math.isfinite(duration) and 0 <= duration < 1e12:
                text = (text + '\n' if text else '') + f'CLI 보고 소요 시간: {duration:g}ms'
            identity = frame or self._digest(str(parent) + ':' + str(data.get('subtype')) + ':' + text)
            self._row('result:' + identity, 'error' if error else 'result', 'CLI 오류' if error else 'CLI 결과', text, parent=parent)

    def _stream(self, event):
        if not isinstance(event, dict):
            return
        kind = event.get('type')
        if kind == 'message_start':
            message = event.get('message')
            mid = _identifier(message.get('id')) if isinstance(message, dict) else None
            if not mid or not self._message_allowed(mid):
                self.message_id = None
                return
            if self.message_id != mid:
                for block in self.blocks.values():
                    self._flush(block)
                self.blocks.clear()
            self.message_id = mid
            return
        index = event.get('index')
        if self.message_id is None or type(index) is not int or not 0 <= index < 10000:
            return
        block = self.blocks.get(index)
        if kind == 'content_block_stop':
            if block:
                self._flush(block)
                block['complete'] = True
                if not block['full_seen']:
                    block['digest'] = block['hasher'].hexdigest()
                self._remember(self.root_texts, block['digest'])
                block['text'] = ''
            return
        value = event.get('content_block') if kind == 'content_block_start' else event.get('delta')
        if not isinstance(value, dict) or value.get('type') not in {'text', 'text_delta'} or not isinstance(value.get('text'), str):
            return
        if block and block['complete']:
            return
        if block is None:
            if len(self.blocks) >= 64:
                _, removed = self.blocks.popitem(last=False)
                self._flush(removed)
            stamp = self.clock()
            block = self.blocks[index] = dict(key='stream:' + self._digest(self.message_id + ':' + str(index)), text='', time=stamp,
                                             last=stamp, complete=False, full_seen=False, truncated=False,
                                             hasher=hashlib.sha256(), digest=None)
        # Only text_delta content is buffered, never raw frames or thinking.
        # Sanitize the assembled public block at flush so credentials split over
        # several token deltas are still recognized as one secret.
        raw = (block['text'] + value['text']).encode('utf-8', errors='replace')
        block['hasher'].update(value['text'].encode('utf-8', errors='replace'))
        block['text'] = raw[:MAX_RECORD_BYTES].decode('utf-8', errors='ignore')
        block['truncated'] |= len(raw) > MAX_RECORD_BYTES
        # Complete public blocks are published at block stop or AssistantMessage;
        # partial credential strings are never persisted between token fragments.

    def _system(self, data, parent):
        subtype = data.get('subtype')
        if subtype in {'task_started', 'task_updated', 'task_progress', 'task_notification'}:
            task_id = _identifier(data.get('task_id'))
            if task_id:
                key = 'task:' + task_id
                if self.tombstones.get(key) not in (None, self.run_id):
                    return
                self._remember(self.tombstones, key, self.run_id, maximum=20000)
            fields = ('task_id', 'task_type', 'description', 'status', 'summary', 'output_file', 'last_tool_name')
            payload = {k: data[k] for k in fields if k in data}
            if isinstance(data.get('patch'), dict):
                payload['patch'] = {k: data['patch'][k] for k in ('status', 'description', 'summary') if k in data['patch']}
            label, kind = '작업자 진행', 'task'
        elif subtype in {'hook_started', 'hook_progress', 'hook_response'}:
            fields = ('hook_id', 'hook_name', 'hook_event', 'stdout', 'stderr', 'output', 'exit_code', 'outcome')
            payload = {k: data[k] for k in fields if k in data}
            label, kind = '후크 진행', 'hook'
        elif subtype == 'status' and data.get('status') == 'compacting':
            payload, label, kind = {'status': 'compacting'}, '대화 맥락 정리', 'status'
        elif subtype == 'compact_boundary':
            payload, label, kind = {'status': 'compacted'}, '대화 맥락 정리 완료', 'status'
        else:
            return
        text = _display_input(payload)
        key = 'system:' + (_identifier(data.get('uuid')) or self._digest(str(parent) + ':' + str(subtype) + ':' + text))
        self._row(key, kind, label, text, parent=parent)
