"""App-owned task visibility; never changes conversation or workspace files."""
import json
from pathlib import Path
import uuid

from .history import HistoryStore, MAX_SESSIONS, read, safe


class SessionVisibility:
    def __init__(self, state):
        self.path = Path(state) / 'session-visibility.json'
        self.hidden = set()
        self.warning = None
        try:
            if safe(self.path).exists():
                value = read(self.path, 64 * 1024)
                ids = value.get('hiddenIds') if isinstance(value, dict) else None
                if (not isinstance(value, dict) or value.get('schemaVersion') != 1
                        or not isinstance(ids, list) or len(ids) > MAX_SESSIONS
                        or any(not isinstance(sid, str) or str(uuid.UUID(sid)) != sid for sid in ids)
                        or len(set(ids)) != len(ids)):
                    raise ValueError('Invalid task visibility')
                self.hidden = set(ids)
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            self.warning = '업무 목록 표시 기록을 읽지 못해 원본을 보존했습니다. 목록 삭제는 잠시 사용할 수 없습니다.'

    def contains(self, sid):
        return sid in self.hidden

    def set_hidden(self, sid, hidden):
        if self.warning:
            raise ValueError(self.warning)
        if not isinstance(sid, str) or str(uuid.UUID(sid)) != sid or type(hidden) is not bool:
            raise ValueError('목록에서 변경할 업무를 확인해 주세요.')
        changed = self.hidden | {sid} if hidden else self.hidden - {sid}
        if len(changed) > MAX_SESSIONS:
            raise ValueError('업무 목록 표시 기록의 보관 한도에 도달했습니다.')
        if changed == self.hidden:
            return
        payload = json.dumps({'schemaVersion': 1, 'hiddenIds': sorted(changed)}).encode('ascii')
        HistoryStore(self.path.parent)._write(self.path, payload)
        self.hidden = changed
