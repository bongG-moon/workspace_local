"""App-owned execution preference and explicit cooperative privilege restart."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from .history import HistoryStore, read, safe
from .windows_process import powershell_path

MODES = {'normal', 'administrator'}
# The version stays 0.23.20; this protocol revision distinguishes corrected
# launch/capture behavior from the original 0.23.20 running in another folder.
EXECUTION_MODE_PROTOCOL = 2
ROOT = Path(__file__).resolve().parents[1]


def checked_mode(value):
    if not isinstance(value, str) or value not in MODES:
        raise ValueError('실행 권한은 일반 또는 관리자를 선택해 주세요.')
    return value


def save_mode(state, mode):
    """Called only after a verified process and desktop have become ready."""
    mode = checked_mode(mode)
    path = safe(Path(state) / 'execution-mode.json')
    HistoryStore(path.parent)._write(path, json.dumps({'schemaVersion': 1, 'mode': mode}).encode('utf-8'))


class ExecutionController:
    def __init__(self, app, *, runner=subprocess.run, clock=time.monotonic, thread_factory=threading.Thread, coordinator_popen=subprocess.Popen):
        self.app, self.runner, self.clock = app, runner, clock
        self.thread_factory = thread_factory
        self.coordinator_popen = coordinator_popen
        self.pending = None
        self.error = None

    def snapshot(self):
        with self.app.lock:
            pending = self.pending
            phase = 'idle'
            if pending:
                try:
                    result = read(self.app.state / 'upgrade-last-result.json', 4096)
                except (ValueError, OSError):
                    result = {}
                if not isinstance(result, dict):
                    result = {}
                handoff = self.app.upgrade.pending
                own_handoff = handoff and handoff['requestId'] == pending['requestId']
                # The elevated coordinator can fail while the original launcher
                # still waits. Its matched result is authoritative immediately.
                if (result.get('requestId') == pending['requestId']
                        and result.get('status') in {'failed', 'expired', 'cancelled'}
                        and not (own_handoff and handoff['stage'] in {'committing', 'closed'})):
                    if own_handoff and handoff['stage'] not in {'failed', 'expired', 'cancelled'}:
                        self.app.upgrade.action({'action':'cancel', 'requestId':pending['requestId']})
                    self.error = ('권한 전환을 취소했습니다. 기존 앱에서 계속 사용할 수 있습니다.'
                                  if result['status'] == 'cancelled' else
                                  '권한 전환 연결을 준비하지 못했습니다. 기존 앱과 작성 중인 내용은 유지합니다. 설정에서 다시 시도해 주세요.')
                    self.pending = None
                elif own_handoff:
                    if handoff['stage'] in {'cancelled', 'expired', 'failed'}:
                        self.error = handoff.get('error') or ('권한 전환을 취소했습니다.' if handoff['stage'] == 'cancelled' else '권한 전환을 마치지 못했습니다. 기존 앱에서 계속 사용할 수 있습니다.')
                        self.pending = None
                    else:
                        phase = {'waiting':'waiting_for_work', 'capture':'preserving_drafts',
                                 'captured':'restarting', 'committing':'restarting', 'closed':'restarting'}.get(handoff['stage'], 'coordinating')
                elif pending['state'] == 'waiting' and self.clock() - pending['acceptedAt'] > 15:
                    if result.get('requestId') == pending['requestId'] or self.clock() - pending['acceptedAt'] > 90:
                        self.error = '권한 전환 연결을 확인하지 못했습니다. 기존 앱을 유지합니다. 설정에서 다시 시도해 주세요.'
                        self.pending = None
                if self.pending and phase == 'idle':
                    phase = 'requesting' if pending['state'] == 'requesting' else 'coordinating'
            return {'current': self.app.execution_mode, 'supported': os.name == 'nt',
                    'state': self.pending['state'] if self.pending else 'idle',
                    'phase': phase if self.pending else 'idle',
                    'target': self.pending['mode'] if self.pending else None, 'error': self.error}

    def start(self, mode):
        mode = checked_mode(mode)
        with self.app.lock:
            status = self.snapshot()
            if os.name != 'nt':
                raise ValueError('실행 권한 전환은 Windows 앱에서 사용할 수 있습니다.')
            if self.pending or self.app._shutdown_state != 'running':
                raise ValueError('이미 권한 전환 또는 종료를 준비하고 있습니다.')
            if self.app.upgrade.pending and self.app.upgrade.pending['stage'] not in {'cancelled', 'expired', 'failed'}:
                raise ValueError('진행 중인 앱 전환을 마친 뒤 권한을 변경해 주세요.')
            if mode == status['current']:
                return status
            from .upgrade_launcher import preflight
            preflight(no_browser=self.app._upgrade_headless)
            request = {'requestId': uuid.uuid4().hex, 'mode': mode, 'state': 'requesting', 'acceptedAt': self.clock()}
            self.pending, self.error = request, None
            self.thread_factory(target=self._launch, args=(request,), daemon=True, name='workspace-execution-restart').start()
            return self.snapshot()

    def _launch(self, request):
        state = self.app.state.parent if self.app.demo else self.app.state
        if self.app.execution_mode == 'administrator' and request['mode'] == 'normal':
            self._normal_successor(request, state)
            return
        arguments = [powershell_path(), '-NoLogo', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                     '-File', str(ROOT / 'deploy/Start-CompanyWorkspace.ps1'),
                     '-ExecutionMode', request['mode'], '-ExecutionRequestId', request['requestId'],
                     '-PythonCommand', sys.executable, '-StateRoot', str(state)]
        if self.app.demo:
            arguments.append('-Demo')
        if self.app._upgrade_headless:
            arguments.append('-NoBrowser')
        try:
            result = self.runner(arguments, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=180,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode != 0:
                raise RuntimeError('launch rejected')
        except (OSError, RuntimeError, subprocess.SubprocessError):
            with self.app.lock:
                if self.pending is request:
                    self.pending = None
                    self.error = '실행 권한 전환을 취소했거나 Windows가 허용하지 않았습니다. 기존 앱과 작업을 유지합니다.'
            return
        with self.app.lock:
            if self.pending is request:
                request.update(state='waiting', acceptedAt=self.clock())

    def _normal_successor(self, request, state):
        # Close/capture the high app while the coordinator is still high. The
        # successor drops privileges only after the old endpoint has stopped.
        arguments=[sys.executable,'-X','utf8','-m','local_app.upgrade_launcher','--state',str(state),
                   '--python',sys.executable,'--request-id',request['requestId'],
                   '--execution-mode','administrator','--target-execution-mode','normal']
        if self.app.demo:arguments.append('--demo')
        if self.app._upgrade_headless:arguments.append('--no-browser')
        try:
            process=self.coordinator_popen(arguments,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            ack=self.app.state/('upgrade-launch-'+request['requestId']+'.json')
            deadline=self.clock()+5
            while self.clock()<deadline:
                if ack.exists():
                    value=read(ack,4096)
                    if value.get('requestId')==request['requestId'] and value.get('status') in {'accepted','already_waiting'}:
                        safe(ack).unlink()
                        with self.app.lock:
                            if self.pending is request:request.update(state='waiting',acceptedAt=self.clock())
                        return
                if process.poll() is not None:break
                time.sleep(.05)
            raise RuntimeError('Coordinator not acknowledged')
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
            with self.app.lock:
                if self.pending is request:
                    self.pending=None
                    self.error='일반 권한으로 전환할 연결을 준비하지 못했습니다. 기존 앱을 유지합니다.'
