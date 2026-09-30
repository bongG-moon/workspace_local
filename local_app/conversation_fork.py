"""Bounded, read-only planning for native Claude conversation forks.

The app stores only a display mirror and a source fingerprint. Claude owns the
actual transcript copy through --resume/--fork-session/--session-id. No prompt,
tool request, trust decision, queue, schedule or control override is copied.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import re
import time
import uuid

from .history import safe
from .session_import import MAX_FILE_BYTES, SessionImporter, SessionImportError, session_uuid
from .windows_paths import workspace_path


class ConversationForkError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fork_capability(info):
    """Advertise only the native flags verified in this installation's help."""
    help_text = info.get('help', '') if isinstance(info, dict) else ''
    help_text = help_text if isinstance(help_text, str) else ''
    available = all(re.search(r'(?<![\w-])' + re.escape(flag) + r'(?=[\s=<\[]|$)', help_text)
                    for flag in ('--resume', '--fork-session', '--session-id'))
    return {'available': available, 'scope': 'latest', 'atMessage': False,
            'reason': ('완료된 대화의 마지막 기록에서 독립된 Claude 세션으로 나눕니다. 파일은 같은 폴더를 사용합니다.'
                       if available else '현재 Claude Code에서 독립 대화 분기 옵션을 확인하지 못했습니다.')}


def _root(value):
    path = safe(Path(value).expanduser().absolute())
    return path


def _same_path(left, right):
    return os.path.normcase(str(Path(left).absolute())) == os.path.normcase(str(Path(right).absolute()))


def normalize_branch(value):
    """A strict history-safe projection; corrupt metadata must never resume a parent."""
    if not isinstance(value, dict) or value.get('scope') != 'latest' or value.get('status') not in {'pending', 'active'}:
        raise ConversationForkError('invalid_branch', '대화 분기 정보를 확인하지 못했습니다. 원본 업무에서 다시 분기해 주세요.')
    result = {key: session_uuid(value.get(key)) for key in ('sourceTaskId', 'sourceSessionId', 'childSessionId')}
    if result['sourceSessionId'] == result['childSessionId']:
        raise ConversationForkError('invalid_branch', '대화 분기의 원본과 새 세션 ID가 같습니다.')
    fingerprint = value.get('sourceFingerprint')
    if not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
        raise ConversationForkError('invalid_branch', '대화 분기의 원본 확인 정보가 없습니다.')
    config = value.get('configRoot')
    if (not isinstance(config, str) or not 0 < len(config) <= 8192 or not Path(config).is_absolute()
            or any(ord(char) < 32 for char in config)):
        raise ConversationForkError('invalid_branch', '대화 분기의 Claude 설정 위치를 확인하지 못했습니다.')
    title, stamp = value.get('sourceTitle'), value.get('createdAt')
    if not isinstance(title, str) or len(title) > 200 or type(stamp) not in (int, float) or not math.isfinite(stamp):
        raise ConversationForkError('invalid_branch', '대화 분기의 이름과 시각을 확인하지 못했습니다.')
    result.update(scope='latest', status=value['status'], sourceFingerprint=fingerprint,
                  configRoot=config, sourceTitle=title, createdAt=stamp)
    return result


def _source_fingerprint(reader, session_id):
    # Exact-ID lookup reuses the importer's bounded, non-link project search.
    # Hash in 64 KiB chunks rather than retaining another transcript in memory.
    deadline = time.monotonic() + reader.seconds
    paths, truncated = reader._paths(deadline, session_id)
    if truncated or len(paths) != 1:
        raise ConversationForkError('source_unavailable', '분기할 Claude 원본 기록 하나를 확인하지 못했습니다. 기록을 다시 확인해 주세요.')
    path, _ = paths[0]
    before = safe(path).stat()
    if before.st_size > MAX_FILE_BYTES:
        raise ConversationForkError('source_limit', '대화 기록이 32 MiB 확인 범위를 넘습니다. 원본 Claude Code에서 분기해 주세요.')
    digest, total = hashlib.sha256(), 0
    with safe(path).open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ConversationForkError('source_changed', '원본 기록이 변경됐습니다. 작업이 끝난 뒤 다시 분기해 주세요.')
        while chunk := stream.read(65536):
            total += len(chunk)
            if total > MAX_FILE_BYTES or time.monotonic() > deadline:
                raise ConversationForkError('source_limit', '대화 분기 확인 범위를 넘었습니다. 원본 Claude Code에서 분기해 주세요.')
            digest.update(chunk)
    after = safe(path).stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ConversationForkError('source_changed', '원본 기록이 변경됐습니다. 작업이 끝난 뒤 다시 분기해 주세요.')
    return digest.hexdigest()


def prepare_fork(parent, info, config_root):
    """Return a fresh untrusted child seed, without starting Claude or writing files.

    Caller holds its session lock and prevents source dispatch until this returns.
    Only completed/idle sessions are eligible; stopped partial turns are excluded.
    The UI mirror is bounded by SessionImporter (150 messages / 500k characters).
    """
    if not fork_capability(info)['available']:
        raise ConversationForkError('fork_unavailable', fork_capability(info)['reason'])
    if (parent.get('state') not in {'idle', 'done'} or parent.get('requests') or parent.get('choice')
            or any(parent.get(key) for key in ('_connecting', '_modelUpdating', '_dispatchClaim', '_choiceAnswerClaim'))):
        raise ConversationForkError('source_busy', '현재 요청과 승인·선택을 마친 뒤 대화를 분기해 주세요.')
    bridge = parent.get('bridge')
    if bridge and (bridge.busy or bridge.pending or bridge.tasks or bridge._control_active):
        raise ConversationForkError('source_busy', '현재 Claude 작업을 마친 뒤 대화를 분기해 주세요.')
    root = _root(config_root)
    if parent.get('importedConfigRoot') and not _same_path(root, parent['importedConfigRoot']):
        raise ConversationForkError('config_changed', '원본 대화를 사용한 Claude 설정 위치가 현재 환경과 다릅니다.')
    if parent.get('sessionId') is None:
        raise ConversationForkError('source_unavailable', '아직 저장된 Claude 대화가 없습니다. 첫 요청을 마친 뒤 분기해 주세요.')
    source_id = session_uuid(parent.get('sessionId'))
    source_task = session_uuid(parent.get('id'))
    reader = SessionImporter(root)
    before = _source_fingerprint(reader, source_id)
    record = reader.load(source_id)
    after = _source_fingerprint(reader, source_id)
    if before != after:
        raise ConversationForkError('source_changed', '원본 기록이 변경됐습니다. 작업이 끝난 뒤 다시 분기해 주세요.')
    if not record['workspaceAvailable'] or not _same_path(record['workspace'], parent.get('workspace', '')):
        raise ConversationForkError('workspace_changed', '원본 Claude 대화와 업무 폴더가 다릅니다. 원래 업무 폴더를 확인해 주세요.')
    workspace_path(Path(record['workspace']))
    created = time.time()
    title = re.sub(r'[\x00-\x1f\x7f\ud800-\udfff]', '', str(parent.get('title') or record['title']))[:180]
    target_id = str(uuid.uuid4())
    while target_id == source_id:
        target_id = str(uuid.uuid4())
    return {'id': str(uuid.uuid4()), 'sessionId': None, 'title': title + ' · 분기',
            'workspace': record['workspace'], 'created': created, 'updated': created,
            'pinned': False, 'trusted': False, 'importedConfigRoot': str(root),
            'messages': [{'role': message['role'], 'text': message['text']} for message in record['messages']],
            'branch': {'sourceTaskId': source_task, 'sourceSessionId': source_id, 'childSessionId': target_id,
                       'sourceTitle': title, 'sourceFingerprint': after, 'configRoot': str(root),
                       'createdAt': created, 'scope': 'latest', 'status': 'pending'}}


def validate_fork_source(branch, config_root, workspace):
    branch = normalize_branch(branch)
    root = _root(config_root)
    if not _same_path(root, branch['configRoot']):
        raise ConversationForkError('config_changed', '이 대화를 분기한 Claude 설정 위치가 현재 연결과 다릅니다.')
    reader = SessionImporter(root)
    if _source_fingerprint(reader, branch['sourceSessionId']) != branch['sourceFingerprint']:
        raise ConversationForkError('source_changed', '분기를 만든 뒤 원본 대화가 변경됐습니다. 원본 업무에서 새 분기를 만들어 주세요.')
    record = reader.load(branch['sourceSessionId'])
    if not record['workspaceAvailable'] or not _same_path(record['workspace'], workspace):
        raise ConversationForkError('workspace_changed', '분기의 원래 업무 폴더를 확인해 주세요.')
    if _source_fingerprint(reader, branch['sourceSessionId']) != branch['sourceFingerprint']:
        raise ConversationForkError('source_changed', '원본 기록이 변경 중입니다. 작업이 끝난 뒤 다시 분기해 주세요.')
    return branch


def branch_connection(item, info, config_root):
    """Return validated ClaudeSession kwargs, including its ``resume`` argument.

    Pending branches survive restart without pretending a not-yet-written child
    transcript exists. Once Claude has written that child, resume only it; never
    replace a damaged/missing active child with a new fork of the source.
    """
    if 'branch' not in item:
        return {'resume': item.get('sessionId')}
    branch = normalize_branch(item['branch'])
    root = _root(config_root)
    if not _same_path(root, branch['configRoot']):
        raise ConversationForkError('config_changed', '이 대화를 분기한 Claude 설정 위치가 현재 연결과 다릅니다.')
    if item.get('sessionId') not in (None, branch['childSessionId']):
        raise ConversationForkError('identity_changed', '분기 대화의 세션 ID가 다릅니다. 원본 대화로 대신 연결하지 않습니다.')
    try:
        child = SessionImporter(root).load(branch['childSessionId'])
    except SessionImportError as exc:
        if exc.code != 'not_found':
            raise
        child = None
    if child is not None:
        if not child['workspaceAvailable'] or not _same_path(child['workspace'], item['workspace']):
            raise ConversationForkError('workspace_changed', '분기 대화의 원래 업무 폴더를 확인해 주세요.')
        return {'resume': branch['childSessionId']}
    if branch['status'] == 'active':
        raise ConversationForkError('child_missing', '분기된 Claude 기록을 찾지 못했습니다. 원본 대화로 대신 연결하지 않습니다.')
    if not fork_capability(info)['available']:
        raise ConversationForkError('fork_unavailable', fork_capability(info)['reason'])
    validate_fork_source(branch, root, item['workspace'])
    return {'resume': branch['sourceSessionId'], 'fork_session': True, 'new_session_id': branch['childSessionId']}
