"""App-owned manual task order, separate from CLI transcripts and history schema."""
import json
from pathlib import Path
import uuid

from .history import HistoryStore, MAX_SESSIONS, read, safe


class SessionOrder:
    def __init__(self, state):
        self.path = Path(state) / 'session-order.json'
        self.ids = None
        self.warning = None
        try:
            if safe(self.path).exists():
                value = read(self.path, 64 * 1024)
                ids = value.get('ids') if isinstance(value, dict) else None
                if (not isinstance(value, dict) or value.get('schemaVersion') != 1
                        or not isinstance(ids, list) or len(ids) > MAX_SESSIONS
                        or any(not isinstance(sid, str) or str(uuid.UUID(sid)) != sid for sid in ids)
                        or len(set(ids)) != len(ids)):
                    raise ValueError('Invalid task order')
                self.ids = ids
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            self.warning = '저장된 업무 순서를 읽지 못했습니다. 원본을 보존했으며 순서 변경을 중지했습니다.'

    def ordered(self, items):
        items = list(items)
        if self.ids is None:
            return sorted(items, key=lambda item: (item.get('pinned') is not True,
                          -item.get('updated', item['created']), item['id']))
        ranks = {sid: index for index, sid in enumerate(self.ids)}
        # Newly created/imported tasks precede saved tasks in their own group.
        # Their creation time, never later activity, determines this initial place.
        return sorted(items, key=lambda item: (item.get('pinned') is not True,
                      1 if item['id'] in ranks else 0,
                      ranks.get(item['id'], -item['created']), item['id']))

    def snapshot(self):
        return {'manual': self.ids is not None, 'ids': list(self.ids or []), 'warning': self.warning}

    def move(self, items, sid, target, position):
        if self.warning:
            raise ValueError(self.warning)
        if not isinstance(sid, str) or not isinstance(target, str) or position not in ('before', 'after'):
            raise ValueError('이동할 업무와 위치를 확인해 주세요.')
        ordered = self.ordered(items)
        by_id = {item['id']: item for item in ordered}
        if sid == target or sid not in by_id or target not in by_id:
            raise ValueError('서로 다른 두 업무 사이에서 순서를 변경해 주세요.')
        if (by_id[sid].get('pinned') is True) != (by_id[target].get('pinned') is True):
            raise ValueError('고정한 업무와 일반 업무는 각각 같은 그룹 안에서 이동해 주세요.')
        ids = [item['id'] for item in ordered if item['id'] != sid]
        ids.insert(ids.index(target) + (position == 'after'), sid)
        payload = json.dumps({'schemaVersion': 1, 'ids': ids}, ensure_ascii=True).encode('ascii')
        # The in-memory order changes only after atomic persistence succeeds.
        HistoryStore(self.path.parent)._write(self.path, payload)
        self.ids = ids
        return self.snapshot()
