"""App-owned conversation spaces and durable, bounded cleanup requests.

These paths are never selected by the user. Originals, exported files and the
CLI's own transcript/configuration remain outside this store's ownership.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import uuid

from .history import HistoryStore, read, safe

CHAT_PROFILE = 'chat-v1'
MAX_PENDING = 10500


def identifier(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('일반 대화 식별자를 확인해 주세요.')
    return value


class ChatSpaces:
    def __init__(self, state):
        self.state = Path(state).absolute()
        self.root = self.state / 'chat-spaces'
        self.journal = self.state / 'chat-deletions.json'
        self.lock = threading.RLock()
        self.pending = {}
        self.warning = None
        try:
            if safe(self.journal).exists():
                value = read(self.journal, 2 * 1024 * 1024)
                if not isinstance(value, dict) or value.get('schemaVersion') != 1 or not isinstance(value.get('pending'), list) or len(value['pending']) > MAX_PENDING:
                    raise ValueError('대화 정리 기록 형식을 확인해 주세요.')
                for row in value['pending']:
                    sid = identifier(row['id'])
                    if sid in self.pending:
                        raise ValueError('중복된 대화 정리 요청입니다.')
                    self.pending[sid] = {'id': sid}
        except (OSError, ValueError, TypeError, KeyError):
            self.warning = '대화 삭제 기록을 읽지 못해 일반 대화 생성·삭제를 보류했어요. 기존 파일은 유지됩니다.'

    def path(self, sid):
        target = safe(self.root / identifier(sid))
        if target.parent != self.root or not target.is_absolute():
            raise ValueError('대화 저장 위치를 확인하지 못했습니다.')
        return target

    def create(self, sid):
        with self.lock:
            if self.warning:
                raise ValueError(self.warning)
            target = self.path(sid)
            safe(self.root).mkdir(parents=True, exist_ok=True)
            target.mkdir(exist_ok=False)
            try:
                owner = {'schemaVersion': 1, 'id': sid, 'profile': CHAT_PROFILE}
                HistoryStore(self.state)._write(target / '.owner.json', json.dumps(owner).encode('utf-8'))
                safe(target / 'workspace').mkdir()
                safe(target / 'cache').mkdir()
            except BaseException:
                # Keep a failed owned allocation for diagnosis, never guess
                # ownership and recursively delete an incompletely marked path.
                raise
            return target / 'workspace'

    def validate(self, sid, workspace):
        with self.lock:
            if self.warning:
                raise ValueError(self.warning)
            target = self.path(sid)
            expected = target / 'workspace'
            if Path(workspace).absolute() != expected:
                raise ValueError('일반 대화의 전용 저장 위치가 달라 연결하지 않았습니다.')
            owner = read(target / '.owner.json', 4096)
            if owner != {'schemaVersion': 1, 'id': sid, 'profile': CHAT_PROFILE}:
                raise ValueError('일반 대화 저장 공간의 소유 정보를 확인하지 못했습니다.')
            if safe(expected).resolve(strict=True) != expected or not expected.is_dir():
                raise ValueError('일반 대화의 저장 공간이 변경되었습니다.')
            return target

    def _save(self):
        HistoryStore(self.state)._write(self.journal, json.dumps({'schemaVersion': 1, 'pending': list(self.pending.values())}).encode('utf-8'))

    def begin_delete(self, sid, workspace):
        with self.lock:
            self.validate(sid, workspace)
            if len(self.pending) >= MAX_PENDING and sid not in self.pending:
                raise ValueError('보류 중인 대화 정리를 먼저 확인해 주세요.')
            self.pending[sid] = {'id': sid}
            try:
                self._save()
            except BaseException:
                self.pending.pop(sid, None)
                raise

    def remove_space(self, sid):
        """Delete only a marked UUID tree, rejecting reparse points throughout."""
        with self.lock:
            if sid not in self.pending or self.warning:
                raise ValueError('확인된 대화 삭제 요청이 없습니다.')
            target = self.path(sid)
            if not target.exists():
                return
            owner = read(target / '.owner.json', 4096)
            if owner != {'schemaVersion': 1, 'id': sid, 'profile': CHAT_PROFILE}:
                raise ValueError('대화 폴더의 소유 정보를 확인하지 못해 정리를 보류했어요.')
            files, directories = [], []
            for base, dirs, names in os.walk(target, followlinks=False):
                base = safe(Path(base))
                if not base.is_relative_to(target) or len(base.relative_to(target).parts) > 32:
                    raise ValueError('대화 폴더의 정리 범위를 확인하지 못했습니다.')
                for name in dirs:
                    directories.append(safe(base / name))
                for name in names:
                    entry = safe(base / name)
                    if entry != target / '.owner.json':
                        files.append(entry)
                if len(files) + len(directories) > 10000:
                    raise ValueError('대화 전용 폴더에 파일이 너무 많아 자동 정리를 보류했어요.')
            # Each canonical path is rechecked immediately before mutation.
            for entry in files:
                safe(entry).unlink(missing_ok=True)
            for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                safe(directory).rmdir()
            safe(target / '.owner.json').unlink()
            try:
                safe(target).rmdir()
            except OSError:
                # A concurrently created/locked directory must remain marked
                # so a later bounded retry can verify the same owner.
                HistoryStore(self.state)._write(safe(target / '.owner.json'), json.dumps(owner).encode('utf-8'))
                raise

    def finish_delete(self, sid):
        with self.lock:
            value = self.pending.pop(identifier(sid), None)
            try:
                self._save()
            except BaseException:
                if value:
                    self.pending[sid] = value
                raise

    def snapshot(self):
        return {'pendingCount': len(self.pending), 'warning': self.warning,
                'message': ('일부 대화 파일 정리가 보류되어 있어요. 다음 앱 실행 때 다시 정리합니다.' if self.pending else '')}
