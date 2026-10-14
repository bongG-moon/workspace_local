"""Small, app-owned virtual task folders; never moves a workspace or transcript."""
import json
from pathlib import Path
import threading
import unicodedata
import uuid

from .history import HistoryStore, MAX_STORED_SESSIONS, read, safe

MAX_GROUPS = 100
MAX_NAME = 80
MAX_BYTES = 2 * 1024 * 1024


class GroupRevisionConflict(ValueError):
    code = 'session_groups_changed'


def checked_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('업무 또는 폴더를 다시 선택해 주세요.')
    return value


def clean_name(value):
    if not isinstance(value, str):
        raise ValueError('폴더 이름을 입력해 주세요.')
    name = unicodedata.normalize('NFC', value.strip())
    if not name or len(name) > MAX_NAME or any(unicodedata.category(char).startswith('C') for char in name):
        raise ValueError('폴더 이름은 1~80자로 입력해 주세요. 줄바꿈과 제어 문자는 사용할 수 없어요.')
    return name


class SessionGroups:
    def __init__(self, state):
        self.path = Path(state) / 'session-groups.json'
        self.groups, self.assignments, self.revision = [], {}, 0
        self.warning = None
        self.lock = threading.RLock()
        try:
            if safe(self.path).exists():
                value = read(self.path, MAX_BYTES)
                if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'groups', 'assignments', 'revision'}
                        or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
                        or type(value['revision']) is not int or not 0 <= value['revision'] < 2**53
                        or not isinstance(value['groups'], list) or len(value['groups']) > MAX_GROUPS
                        or not isinstance(value['assignments'], dict) or len(value['assignments']) > MAX_STORED_SESSIONS):
                    raise ValueError('Invalid task folders')
                ids, names = set(), set()
                for group in value['groups']:
                    if (not isinstance(group, dict) or set(group) != {'id', 'name', 'collapsed'}
                            or type(group['collapsed']) is not bool):
                        raise ValueError('Invalid task folder')
                    gid, name = checked_id(group['id']), clean_name(group['name'])
                    if name != group['name'] or gid in ids or name.casefold() in names:
                        raise ValueError('Duplicate task folder')
                    ids.add(gid)
                    names.add(name.casefold())
                for sid, gid in value['assignments'].items():
                    if checked_id(sid) and checked_id(gid) not in ids:
                        raise ValueError('Unknown task folder')
                self.groups, self.assignments, self.revision = value['groups'], value['assignments'], value['revision']
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            self.warning = '업무 폴더 정보를 읽지 못해 원본을 보존했습니다. 폴더 변경은 잠시 사용할 수 없어요.'

    def snapshot(self):
        with self.lock:
            return {'groups': [dict(group) for group in self.groups], 'assignments': dict(self.assignments),
                    'revision': self.revision, 'warning': self.warning}

    def group_for(self, sid):
        with self.lock:
            return self.assignments.get(sid)

    @staticmethod
    def unique_name(groups, name, exclude=None):
        existing = {group['name'].casefold() for group in groups if group['id'] != exclude}
        candidate, number = name, 2
        while candidate.casefold() in existing:
            suffix = ' (' + str(number) + ')'
            candidate = name[:MAX_NAME - len(suffix)].rstrip() + suffix
            number += 1
        return candidate

    def change(self, data, visible_ids):
        """Caller supplies index IDs only, never hydrated session messages."""
        with self.lock:
            if self.warning:
                raise ValueError(self.warning)
            action = data.get('action')
            fields = {'create': {'name'}, 'rename': {'groupId', 'name'}, 'delete': {'groupId'},
                      'collapse': {'groupId', 'collapsed'}, 'move': {'id', 'groupId'}}
            if not isinstance(action, str) or action not in fields or set(data) - {'action', 'revision'} != fields[action]:
                raise ValueError('업무 폴더 변경 요청을 확인해 주세요.')
            if 'revision' in data:
                if type(data['revision']) is not int or data['revision'] < 0:
                    raise ValueError('업무 폴더 목록을 다시 확인해 주세요.')
                if data['revision'] != self.revision:
                    raise GroupRevisionConflict('업무 폴더가 다른 화면에서 변경됐어요. 최신 목록을 확인한 뒤 다시 시도해 주세요.')
            groups = [dict(group) for group in self.groups]
            assignments = dict(self.assignments)
            by_id = {group['id']: group for group in groups}
            gid = data.get('groupId')
            if action != 'create' and not (action == 'move' and gid is None):
                checked_id(gid)
                if gid not in by_id:
                    raise ValueError('폴더를 찾지 못했습니다. 목록을 다시 확인해 주세요.')
            if action == 'create':
                if len(groups) >= MAX_GROUPS:
                    raise ValueError('업무 폴더는 최대 100개까지 만들 수 있어요.')
                name = self.unique_name(groups, clean_name(data['name']))
                groups.append({'id': str(uuid.uuid4()), 'name': name, 'collapsed': False})
            elif action == 'rename':
                by_id[gid]['name'] = self.unique_name(groups, clean_name(data['name']), gid)
            elif action == 'delete':
                groups = [group for group in groups if group['id'] != gid]
                assignments = {sid: group for sid, group in assignments.items() if group != gid}
            elif action == 'collapse':
                if type(data['collapsed']) is not bool:
                    raise ValueError('폴더의 펼침 상태를 확인해 주세요.')
                by_id[gid]['collapsed'] = data['collapsed']
            elif action == 'move':
                sid = checked_id(data['id'])
                if sid not in visible_ids:
                    raise ValueError('목록에 표시된 업무만 폴더로 옮길 수 있어요. 보관한 업무는 먼저 복원해 주세요.')
                if gid is None:
                    assignments.pop(sid, None)
                else:
                    assignments[sid] = gid
                if len(assignments) > MAX_STORED_SESSIONS:
                    raise ValueError('업무 폴더 기록의 보관 한도에 도달했습니다.')
            if groups == self.groups and assignments == self.assignments:
                return self.snapshot()
            revision = self.revision + 1
            if revision >= 2**53:
                raise ValueError('업무 폴더 변경 기록의 한도에 도달했습니다.')
            payload = json.dumps({'schemaVersion': 1, 'groups': groups, 'assignments': assignments,
                                  'revision': revision}, ensure_ascii=False).encode('utf-8')
            if len(payload) > MAX_BYTES:
                raise ValueError('업무 폴더 기록의 크기 한도에 도달했습니다.')
            HistoryStore(self.path.parent)._write(self.path, payload)
            self.groups, self.assignments, self.revision = groups, assignments, revision
            return self.snapshot()
