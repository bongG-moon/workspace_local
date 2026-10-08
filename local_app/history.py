"""Small index + bounded per-conversation UI mirror; the CLI owns its transcript.

Legacy history is retained, and migrated only on the next normal save. No auth,
permissions, requests, events or trust decision is persisted here.
"""
import json
import math
import os
from pathlib import Path
import time
import uuid

from .executions import normalize_executions, safe_run_id
from .tool_activity import normalize_activities

KEYS = ('id','title','workspace','created','updated','pinned','sessionId')
MAX_SESSIONS = 500
MAX_ARCHIVED_SESSIONS = 10000
MAX_STORED_SESSIONS = MAX_SESSIONS + MAX_ARCHIVED_SESSIONS
MAX_ARTIFACTS = 100
INDEX_METADATA = ('lastRunId', 'branch', 'importedConfigRoot', 'choice', 'verification')


def safe(path):
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('연결된 기록 경로는 사용하지 않습니다.')
    return path


def read(path, limit):
    with safe(path).open('rb') as stream:
        before = os.fstat(stream.fileno())
        if before.st_size < 0 or before.st_size > limit:
            raise ValueError('기록의 확인 범위를 초과했습니다.')
        # BufferedReader can allocate the whole requested bound up front. A
        # tiny index must not reserve 16 MiB just because that is its limit.
        raw = stream.read(min(before.st_size + 1, limit + 1))
        after = os.fstat(stream.fileno())
        if (len(raw) != before.st_size or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns):
            raise ValueError('읽는 동안 기록이 변경되어 원본을 보존했습니다. 다시 시도해 주세요.')
    return json.loads(raw.decode('utf-8-sig'))


def row(item):
    sid = str(uuid.UUID(item['id']))
    if sid != item['id']:
        raise ValueError('대화 ID를 확인하지 못했습니다.')
    result = {key:item.get(key) for key in KEYS}
    for key, maximum in (('title',200),('workspace',8192)):
        if not isinstance(result[key], str) or len(result[key]) > maximum:
            raise ValueError('대화 정보의 형식을 확인해 주세요.')
    if type(result['created']) not in (float,int) or not math.isfinite(result['created']):
        raise ValueError('대화 시각을 확인하지 못했습니다.')
    result['updated'] = item.get('updated', result['created'])
    if type(result['updated']) not in (float, int) or not math.isfinite(result['updated']):
        result['updated'] = result['created']
    result['pinned'] = item.get('pinned') is True
    if result['sessionId'] is not None:
        # Opaque CLI metadata, never a filename or executable argument here.
        # The bridge separately validates an ID before attempting --resume.
        if not isinstance(result['sessionId'], str) or len(result['sessionId']) > 160:
            result['sessionId'] = None
    if not isinstance(item.get('messages'), list):
        raise ValueError('대화 기록 형식을 확인하지 못했습니다.')
    messages = []
    for message in item['messages'][-150:]:
        if not isinstance(message, dict) or message.get('role') not in {'user','assistant'} or not isinstance(message.get('text'),str):
            raise ValueError('대화 메시지를 확인하지 못했습니다.')
        clean = {'role':message['role'], 'text':message['text'][:100000]}
        run_id = safe_run_id(message.get('runId'))
        if run_id is not None:
            clean['runId'] = run_id
        if message.get('role') == 'user' and isinstance(message.get('files'),list):
            clean['files'] = [value[:4096] for value in message['files'][:12] if isinstance(value,str)]
        if (message.get('role') == 'user' and isinstance(message.get('requestId'), str)
                and len(message['requestId']) == 32 and all(c in '0123456789abcdef' for c in message['requestId'])):
            clean['requestId'] = message['requestId']
        messages.append(clean)
    def size(message):
        return len(message['text']) + sum(len(value) for value in message.get('files', []))
    total = sum(size(m) for m in messages)
    while len(messages) > 1 and total > 500000:
        total -= size(messages.pop(0))
    result['messages'] = messages
    artifacts = []
    for artifact in (item.get('artifacts') or [])[-MAX_ARTIFACTS:]:
        if not isinstance(artifact, dict) or artifact.get('change') not in {'created', 'modified'}:
            continue
        if any(not isinstance(artifact.get(key), str) or len(artifact[key]) > maximum
               for key, maximum in (('path', 4096), ('name', 1024), ('runId', 64))):
            continue
        observed = artifact.get('observedAt')
        if type(observed) not in (int, float) or not math.isfinite(observed):
            continue
        artifacts.append({key: artifact[key] for key in ('path', 'name', 'change', 'runId', 'observedAt')} |
                         {'size': artifact.get('size') if type(artifact.get('size')) is int and artifact['size'] >= 0 else None})
    result['artifacts'] = artifacts
    result['executions'] = normalize_executions(item.get('executions'))
    result['toolActivity'] = normalize_activities(item.get('toolActivity'))
    last_run = item.get('lastRunId')
    result['lastRunId'] = last_run if isinstance(last_run, str) and len(last_run) <= 64 else None
    if 'branch' in item:
        from .conversation_fork import normalize_branch
        result['branch'] = normalize_branch(item['branch'])
    imported = item.get('importedConfigRoot')
    if isinstance(imported, str) and 0 < len(imported) <= 8192 and Path(imported).is_absolute():
        result['importedConfigRoot'] = imported
    # A display-only business question may survive a restart. It never restores
    # trust, pending tool permission, or permission-mode overrides.
    if isinstance(item.get('choice'), dict):
        from .choices import normalize
        try:
            result['choice'] = normalize(item['choice'])
        except (ValueError, TypeError):
            pass
    verification = item.get('verification')
    if isinstance(verification, dict) and verification.get('state') in {'checking', 'needs-review', 'unverified'}:
        state = verification['state'] if verification['state'] != 'checking' else 'unverified'
        result['verification'] = {'state': state, 'message': '이전 요청의 결과 확인 상태입니다. 연결 종료 후 추가 확인은 수행되지 않았습니다.'}
    return result


class HistoryStore:
    def __init__(self, root: Path):
        self.root = root
        self.warning = None
        self.migrate = False
        self.index_bytes = None
        self.index_entries = {}

    @staticmethod
    def compact(item):
        """Keep navigation/attention metadata, release dormant conversation bodies."""
        item['_artifactCount'] = len(item.get('artifacts', []))
        item.pop('messages', None)
        item.pop('artifacts', None)
        item.pop('executions', None)
        item.pop('toolActivity', None)
        item['_historyUnloaded'] = True
        return item

    def hydrate(self, item):
        if not item.get('_historyUnloaded'):
            return
        try:
            sid = str(uuid.UUID(item['id']))
            value = row(read(self.root/'history-sessions'/(sid+'.json'), 8*1024*1024))
            if value['id'] != sid:
                raise ValueError('대화 파일과 목록이 다릅니다.')
            if item.pop('_historyIndexOnly', False):
                # Bodies are authoritative if a crash happened between the body
                # and index atomic writes. Do not override live metadata on LRU
                # reloads, which never carry this startup-only marker.
                item.update({key: value[key] for key in KEYS})
                for key in INDEX_METADATA:
                    if key in value:
                        item[key] = value[key]
                    else:
                        item.pop(key, None)
            item.pop('_historyMetadataPending', None)
            item['messages'] = value['messages']
            item['artifacts'] = value['artifacts']
            item['executions'] = normalize_executions(value['executions'], interrupted=True)
            item['toolActivity'] = normalize_activities(value['toolActivity'], interrupted=True)
            item.pop('_historyUnloaded', None)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            self.warning = '대화 기록을 다시 읽지 못해 원본을 보존했습니다. 앱을 다시 열어 기록 위치를 확인해 주세요.'
            raise ValueError(self.warning) from exc

    def load(self, *, lazy=False):
        index = self.root/'history-index.json'
        legacy = self.root/'history.json'
        result = []
        try:
            if safe(index).exists():
                obj = read(index, 16*1024*1024)
                if not isinstance(obj, dict) or obj.get('schemaVersion') not in (1, 2) or not isinstance(obj.get('sessions'),list) or len(obj['sessions']) > MAX_STORED_SESSIONS:
                    raise ValueError('기록 목록을 확인하지 못했습니다.')
                seen = set()
                cached_entries = {}
                for entry in obj['sessions']:
                    sid = str(uuid.UUID(entry['id']))
                    if sid in seen or sid != entry['id']:
                        raise ValueError('중복된 대화 식별자입니다.')
                    seen.add(sid)
                    if lazy:
                        # Validate bounded navigation metadata, not whole bodies.
                        # The original per-session file is validated on access.
                        item = row(dict(entry, messages=[]))
                        item['_artifactCount'] = entry.get('artifactCount', 0)
                        if type(item['_artifactCount']) is not int or not 0 <= item['_artifactCount'] <= MAX_ARTIFACTS:
                            raise ValueError('결과물 수를 확인하지 못했습니다.')
                        if obj['schemaVersion'] == 1 or entry.get('metadataComplete') is not True:
                            item['_historyMetadataPending'] = True
                        item.pop('messages', None)
                        item.pop('artifacts', None)
                        item.pop('executions', None)
                        item.pop('toolActivity', None)
                        item['_historyUnloaded'] = item['_historySaved'] = True
                        item['_historyIndexOnly'] = True
                        cached_entries[sid] = {key: item[key] for key in (*KEYS, *INDEX_METADATA) if key in item} | {
                            'artifactCount': item['_artifactCount'],
                            'metadataComplete': not item.get('_historyMetadataPending', False)}
                        result.append(item)
                        continue
                    item = row(read(self.root/'history-sessions'/(sid+'.json'), 8*1024*1024))
                    if item['id'] != sid:
                        raise ValueError('대화 파일과 목록이 다릅니다.')
                    item['executions'] = normalize_executions(item['executions'], interrupted=True)
                    item['toolActivity'] = normalize_activities(item['toolActivity'], interrupted=True)
                    result.append(item)
                if lazy and obj['schemaVersion'] == 2:
                    self.index_entries = cached_entries
                    # A loaded index does not need to be reserialized on the
                    # first streamed body event unless metadata actually changed.
                    self.index_bytes = b''
            elif safe(legacy).exists():
                old = read(legacy, 80*1024*1024)
                if not isinstance(old, list) or len(old) > MAX_SESSIONS:
                    raise ValueError('기존 기록 형식을 확인하지 못했습니다.')
                result = [row(item) for item in old]
                for item in result:
                    item['executions'] = normalize_executions(item['executions'], interrupted=True)
                    item['toolActivity'] = normalize_activities(item['toolActivity'], interrupted=True)
                self.migrate = True
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Do not overwrite a damaged index or silently destroy its entries.
            self.warning = '일부 대화 기록을 확인하지 못해 원본을 보존했습니다. 현재 앱의 새 대화 기록 저장은 중지되어 있습니다. Claude 원본 기록은 별개입니다.'
        return result

    def _write(self, target, data):
        safe(target)
        safe(target.parent).mkdir(parents=True, exist_ok=True)
        temp = safe(target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp'))
        try:
            with temp.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Windows scanners can briefly open a freshly written JSON file
            # without delete sharing. Retry only the same atomic replacement,
            # never the write or a user action. Normal saves do not wait.
            for delay in (.01, .025, .05, None):
                safe(target)
                try:
                    temp.replace(target)
                    break
                except OSError as exc:
                    if delay is None or getattr(exc, 'winerror', None) not in {5, 32, 33}:
                        raise
                    time.sleep(delay)
        finally:
            temp.unlink(missing_ok=True)

    def save(self, items, changed_id=None):
        if self.warning:
            return
        items = list(items)
        if len(items) > MAX_STORED_SESSIONS:
            raise ValueError('보관 기록의 안전한 처리 한도에 도달했습니다. 기존 기록은 삭제하지 않았습니다.')
        # Only the changed conversation is normalized/serialized on each event.
        for item in items:
            if item.get('_historyUnloaded'):
                # Its validated body remains on disk. Metadata is unchanged until
                # LocalApp.get hydrates it before a user mutation.
                continue
            if self.migrate or changed_id is None or item['id'] == changed_id:
                value = row(item)
                payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
                self._write(self.root/'history-sessions'/(value['id']+'.json'), payload)
        entries = {}
        for item in items:
            if (changed_id is not None and item['id'] != changed_id
                    and item['id'] in self.index_entries and not self.migrate):
                entries[item['id']] = self.index_entries[item['id']]
                continue
            # row() strips runtime trust, credentials and live permission state.
            summary = row(dict(item, messages=[], artifacts=[], executions=[], toolActivity=[]))
            entry = {key: summary[key] for key in (*KEYS, *INDEX_METADATA) if key in summary}
            entry['artifactCount'] = item.get('_artifactCount', 0) if item.get('_historyUnloaded') else len(item.get('artifacts', []))
            entry['metadataComplete'] = not item.get('_historyMetadataPending', False)
            entries[item['id']] = entry
        if entries != self.index_entries or self.index_bytes is None:
            index = {'schemaVersion': 2, 'sessions': list(entries.values())}
            payload = json.dumps(index, ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(payload) > 16 * 1024 * 1024:
                raise ValueError('목록 정보의 안전한 보관 범위를 초과했습니다. 기존 목록은 보존했습니다.')
            if payload != self.index_bytes:
                self._write(self.root/'history-index.json', payload)
                self.index_bytes = payload
            self.index_entries = entries
        self.migrate = False
