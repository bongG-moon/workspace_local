"""Small index + bounded per-conversation UI mirror; the CLI owns its transcript.

Legacy history is retained, and migrated only on the next normal save. No auth,
permissions, requests, events or trust decision is persisted here.
"""
import json
import math
import os
from pathlib import Path
import uuid

KEYS = ('id','title','workspace','created','updated','pinned','sessionId')
MAX_SESSIONS = 500
MAX_ARTIFACTS = 100


def safe(path):
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('연결된 기록 경로는 사용하지 않습니다.')
    return path


def read(path, limit):
    with safe(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('기록의 확인 범위를 초과했습니다.')
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
        if message.get('role') == 'user' and isinstance(message.get('files'),list):
            clean['files'] = [value[:4096] for value in message['files'][:12] if isinstance(value,str)]
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
    last_run = item.get('lastRunId')
    result['lastRunId'] = last_run if isinstance(last_run, str) and len(last_run) <= 64 else None
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

    def load(self):
        index = self.root/'history-index.json'
        legacy = self.root/'history.json'
        result = []
        try:
            if safe(index).exists():
                obj = read(index, 16*1024*1024)
                if not isinstance(obj, dict) or obj.get('schemaVersion') != 1 or not isinstance(obj.get('sessions'),list) or len(obj['sessions']) > MAX_SESSIONS:
                    raise ValueError('기록 목록을 확인하지 못했습니다.')
                seen = set()
                for entry in obj['sessions']:
                    sid = str(uuid.UUID(entry['id']))
                    if sid in seen or sid != entry['id']:
                        raise ValueError('중복된 대화 식별자입니다.')
                    seen.add(sid)
                    item = row(read(self.root/'history-sessions'/(sid+'.json'), 8*1024*1024))
                    if item['id'] != sid:
                        raise ValueError('대화 파일과 목록이 다릅니다.')
                    result.append(item)
            elif safe(legacy).exists():
                old = read(legacy, 80*1024*1024)
                if not isinstance(old, list) or len(old) > MAX_SESSIONS:
                    raise ValueError('기존 기록 형식을 확인하지 못했습니다.')
                result = [row(item) for item in old]
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
            safe(target)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)

    def save(self, items, changed_id=None):
        if self.warning:
            return
        items = list(items)
        if len(items) > MAX_SESSIONS:
            raise ValueError('업무는 최대 500개까지 저장할 수 있습니다. 기존 업무를 이어서 사용해 주세요.')
        # Only the changed conversation is normalized/serialized on each event.
        for item in items:
            if self.migrate or changed_id is None or item['id'] == changed_id:
                value = row(item)
                payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
                self._write(self.root/'history-sessions'/(value['id']+'.json'), payload)
        index = {'schemaVersion':1, 'sessions':[{key:item.get(key) for key in KEYS} for item in items]}
        payload = json.dumps(index, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if payload != self.index_bytes:
            self._write(self.root/'history-index.json', payload)
            self.index_bytes = payload
        self.migrate = False
