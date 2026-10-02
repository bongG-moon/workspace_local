"""Loopback-only UI server. All operational endpoints require a per-run capability."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import unicodedata
from pathlib import Path
import secrets
import subprocess
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
import uuid
import webbrowser

from .bridge import BridgeError, ClaudeSession, ControlRestoreRequired, HIDDEN, probe_cli, resolve_cli, runtime_context
from .claude_inventory import ClaudeInventory
from .history import HistoryStore, MAX_ARTIFACTS, MAX_SESSIONS, safe
from .session_order import SessionOrder
from .session_visibility import SessionVisibility
from .artifacts import changes, linked, snapshot, PREVIEW_TYPES
from .file_preview import build_preview, source_preview_allowed
from .file_diff import FileDiffStore
from .capabilities import catalog
from .completions import CompletionDiscovery, REFERENCE_FILE_TYPES
from .picker_protocol import read_result as read_picker_result
from .picker_channel import private_picker_directory
from .windows_paths import desktop_folder, workspace_path, DESKTOP_UNAVAILABLE
from .windows_process import powershell_path
from .attention import AttentionNotifier, snapshot as attention_snapshot
from .attachments import AttachmentStore, MAX_UPLOAD
from .app_dispatch import DispatchController
from .progress_log import ProgressStore

ASSETS = Path(__file__).parent / "web"
SAFE_FILES = PREVIEW_TYPES
MAX_BODY = 256 * 1024
WORKSPACE_VERSION = "0.23.5"
MANUAL_FILENAME = "WORKSPACE_USER_GUIDE.html"
MANUAL_CSP = (
    "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; font-src data:; "
    "img-src 'none'; connect-src 'none'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'none'"
)
# Closed URL aliases only: legacy documents need not exist on disk.
MANUAL_ALIASES = {
    '/manual/Company-Agent-사용자-안내서.html': '',
    '/manual/handbook': '#handbook',
    '/manual/onboarding': '#onboarding',
    '/manual/usage': '#usage',
    '/manual/commands': '#commands',
    '/manual/Company-Agent-Guide.html': '',
    '/manual/Company-Agent-Handbook.html': '#handbook',
    '/manual/Company-Agent-Onboarding.html': '#onboarding',
    '/manual/Claude-Code-필수-사용법.html': '#commands',
    '/manual/First-Work.html': '#onboarding',
    '/manual/first-work.html': '#onboarding',
}


def workspace_window_handle():
    """Capture only the foreground Workspace window at the user's picker click.

    A background Python process is not a suitable native dialog owner. Do not
    search for or activate arbitrary browser windows if the user has switched.
    HWND is pointer-sized, including on 64-bit Windows.
    """
    if os.name != "nt":
        return 0
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        handle = user32.GetForegroundWindow()
        if not handle or not user32.IsWindowVisible(handle):
            return 0
        title = ctypes.create_unicode_buffer(512)
        if not user32.GetWindowTextW(handle, title, len(title)):
            return 0
        if not title.value.startswith("Company Workspace"):
            return 0
        return int(handle)
    except (OSError, AttributeError, ValueError):
        return 0


def folder(value):
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("작업 폴더를 선택해 주세요.")
    return path


def workspace_folder(item):
    """A saved canonical root must not silently follow a replacement junction."""
    path = Path(item['workspace']).absolute()
    try:
        if path.resolve(strict=True) != path or any(linked(part) for part in (path, *path.parents)):
            raise ValueError('업무 폴더의 실제 위치가 바뀌었습니다. 새 업무에서 폴더를 다시 선택해 주세요.')
    except OSError as exc:
        raise ValueError('업무 폴더를 찾을 수 없습니다. 폴더 위치와 접근 권한을 확인해 주세요.') from exc
    return folder(path)


class LocalApp:
    def __init__(self, state: Path, command=None, info=None, demo=False, *, managed_workspace_root=None):
        self.state = state
        self.state.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        from .ui_health import UiHealthLog
        self.ui_health = UiHealthLog(state, WORKSPACE_VERSION)
        self.lock = threading.RLock()
        self._lifecycle = threading.Condition()
        self._active_operations = 0
        self._shutdown_state = 'running'
        self._close_lock = threading.Lock()
        self.sessions = {}
        self.command, self.info, self.demo = command, info or {}, demo
        self._injected_command = command is not None
        self.notifier = AttentionNotifier(enabled=not demo and not self._injected_command)
        self.tray = None
        self._open_window_callback = None
        self._desktop_window = None
        self._navigation = None
        self._viewed_session = None
        self._viewed_until = 0
        self.attachment_store = AttachmentStore(state)
        self.file_diffs = FileDiffStore(state / 'file-changes')
        self.progress_logs = ProgressStore(state / 'progress')
        self.error = None
        self.workspace_location_error = None
        self.managed_workspace_root = None
        self.default_workspace = None
        if managed_workspace_root is not None or demo:
            # Tests and demo never create a task on the interactive Desktop.
            self.managed_workspace_root = Path(managed_workspace_root or self.state / 'workspaces').absolute()
            self.default_workspace = self.state.absolute()
        else:
            try:
                self.default_workspace = desktop_folder()
                self.managed_workspace_root = self.default_workspace / 'Company Workspace'
            except ValueError:
                self.workspace_location_error = DESKTOP_UNAVAILABLE
        self._isolated_workspace_root = managed_workspace_root is not None or demo
        self.reconnect_lock = threading.Lock()
        self.dialog_lock = threading.Lock()
        self.inventory_client = ClaudeInventory()
        self.completion_discovery = CompletionDiscovery(self.inventory_client)
        if command is None and not demo:
            try:
                self.command = resolve_cli()
                self.info = probe_cli(self.command)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                self.error = str(exc)
        self._load()
        self.dispatch = DispatchController(self)
        from .desktop_notifications import DesktopNotifications
        self.desktop = DesktopNotifications(state, notify=self._notify_desktop,
            is_foreground=self._viewing_task, on_open=self.open_task)
        # Only an explicit --no-browser launch may omit a screen draft ACK.
        # A test/browser client with no native host is not proof of no drafts.
        self._upgrade_headless = False
        from .upgrade_handoff import UpgradeHandoff
        self.upgrade = UpgradeHandoff(self, WORKSPACE_VERSION)
        from .app_updates import UpdateManager
        from .update_source import load_source
        self.app_updates = UpdateManager(state, WORKSPACE_VERSION,
            demo=demo or self._injected_command, installer=self._install_app_update,
            handoff_status=lambda: self.upgrade.status(),
            source=load_source(Path(__file__).resolve().parents[1]))
        self.update_warning = None

    def _install_app_update(self, version, package_bytes, sha256, *, cancel=None):
        from .update_install import stage_and_launch
        # Shutdown closes admission before an updater can launch a successor.
        # The new launcher uses the existing cooperative draft/work handoff.
        with self.operation(upgrade_change=True):
            return stage_and_launch(self.state, WORKSPACE_VERSION, version,
                package_bytes, sha256, demo=self.demo,
                no_browser=self._upgrade_headless, cancel=cancel)

    def _notify_desktop(self, payload, on_click):
        if self._desktop_window is not None:
            result = self._desktop_window.notify(title=payload['title'], message=payload['message'],
                kind=payload['kind'], notification_id=payload['id'], on_click=on_click)
            # A busy or suppressed card must not escape through a second channel.
            # None means this host cannot offer cards (e.g. an older native host).
            if result is not None:
                return 'busy' if result == 'busy' else result is True
        return bool(self.tray and self.tray.notify(title=payload['title'], message=payload['message'], on_click=on_click))

    def _viewing_task(self, sid):
        return (self._viewed_session == sid and time.monotonic() < self._viewed_until
                and self.notifier.is_foreground())

    def _publish_notification(self, sid, title, kind, identity):
        try:
            self.desktop.publish(sid, title, kind, identity)
        except (ValueError, OSError):
            # A desktop delivery problem must not interrupt the CLI reader or
            # turn a completed request into a transport failure.
            pass

    def open_task(self, sid):
        with self.lock:
            self.get(sid)
            self._navigation = {'id': secrets.token_hex(16), 'sessionId': sid}
        if self._open_window_callback:
            self._open_window_callback()

    def import_sessions(self, session_id=None):
        if self.demo or not self.command:
            raise ValueError('기존 Claude Code 연결을 먼저 확인해 주세요.')
        from .session_import import SessionImporter
        root = Path(runtime_context(self.command)['configRoot']).resolve()
        reader = SessionImporter(root)
        return reader.load(session_id) if session_id else reader.discover()

    def import_session(self, session_id):
        from .session_import import session_uuid
        session_id = session_uuid(session_id)
        record = self.import_sessions(session_id)
        config_root = str(Path(runtime_context(self.command)['configRoot']).resolve())
        with self.lock:
            for item in self.sessions.values():
                if (item.get('sessionId') == record['sessionId'] and item['workspace'] == record['workspace']
                        and os.path.normcase(item.get('importedConfigRoot', config_root)) == os.path.normcase(config_root)):
                    if item.get('_removingFromList'):
                        raise ValueError('업무 목록을 정리하고 있습니다. 잠시 뒤 다시 불러와 주세요.')
                    restored = self.session_visibility.contains(item['id'])
                    if restored:
                        self.session_visibility.set_hidden(item['id'], False)
                    return {'ok': True, 'existing': True, 'restored': restored, 'session': self.public(item)}
            if self.history.warning:
                raise ValueError(self.history.warning)
            if len(self.sessions) >= MAX_SESSIONS:
                raise ValueError('업무는 최대 500개까지 저장할 수 있습니다.')
            sid = record['id']
            if sid in self.sessions:
                raise ValueError('같은 세션의 업무 기록이 이미 존재합니다. 기존 업무를 확인해 주세요.')
            item = {key: record[key] for key in ('id', 'sessionId', 'title', 'workspace', 'created', 'updated', 'messages')}
            item['title'] = self.unique_title(item['title'][:100])
            workspace_folder(item)
            item.update(pinned=False, trusted=False, state='idle', seq=0, events=[], requests={},
                        bridge=None, attachments=[], artifacts=[], lastRunId=None, modelOverride=None,
                        importedConfigRoot=str(Path(runtime_context(self.command)['configRoot']).resolve()))
            self.sessions[sid] = item
            try:
                self.save(sid)
            except (OSError, ValueError):
                self.sessions.pop(sid, None)
                raise
            return {'ok': True, 'existing': False, 'session': self.public(item), 'warnings': record.get('warnings', [])}

    def _check_import_context(self, item):
        if item.get('importedConfigRoot'):
            current = str(Path(runtime_context(self.command)['configRoot']).resolve()) if self.command else ''
            if os.path.normcase(current) != os.path.normcase(item['importedConfigRoot']):
                raise ValueError('이 대화를 가져온 Claude 설정 위치가 현재 연결과 다릅니다. 원래 환경으로 앱을 다시 열어 주세요.')

    def _load(self):
        self.history = HistoryStore(self.state)
        self._history_access = {}
        for item in self.history.load(lazy=True):
            item.update(bridge=None, requests={}, events=[], seq=0, trusted=False, state='idle')
            self.sessions[item['id']] = item
        self.session_order = SessionOrder(self.state)
        self.session_visibility = SessionVisibility(self.state)

    def save(self, sid=None):
        with self.lock:
            migrated = self.history.migrate
            for item in self.sessions.values():
                if (migrated or sid is None or item['id'] == sid) and not item.get('_historyUnloaded'):
                    # A failed (or partially successful) write must never make a
                    # newer in-memory result eligible for replacement by old disk data.
                    item['_historySaved'] = False
            self.history.save(self.sessions.values(), sid)
            if not self.history.warning:
                for item in self.sessions.values():
                    if (migrated or sid is None or item['id'] == sid) and not item.get('_historyUnloaded'):
                        item['_historySaved'] = True

    def get(self, sid, *, _internal=False):
        with self.lock:
            if sid not in self.sessions:
                raise ValueError("대화를 찾을 수 없습니다.")
            item = self.sessions[sid]
            if not _internal and item.get('_removingFromList'):
                raise ValueError('업무 목록을 정리하고 있습니다. 잠시 기다려 주세요.')
            if not _internal and self.session_visibility.contains(sid):
                raise ValueError('업무 목록에서 삭제한 항목입니다. Claude 세션이 있는 업무는 새 업무의 기존 세션 활용하기에서 다시 불러올 수 있어요.')
            self.history.hydrate(item)
            self._history_access[sid] = time.monotonic()
            # Active CLI work is never evicted. Dormant UI mirrors use an LRU of
            # eight conversations; full Claude transcripts remain CLI-owned.
            loaded = [row for row in self.sessions.values() if not row.get('_historyUnloaded')]
            for old in sorted(loaded, key=lambda row: self._history_access.get(row['id'], 0)):
                if len(loaded) <= 8:
                    break
                bridge = old.get('bridge')
                if (old['id'] == sid or not old.get('_historySaved')
                        or any(old.get(key) for key in ('_connecting', '_modelUpdating', '_dispatchClaim', '_choiceAnswerClaim'))
                        or old.get('requests')
                        or old.get('state') in {'starting', 'running', 'question', 'approval'}
                        or bridge and (not bridge.closed or getattr(bridge, 'cleanup_complete', False) is not True)
                        or self.history.migrate or self.history.warning):
                    continue
                self.history.compact(old)
                loaded.remove(old)
            return item

    def public(self, item):
        with self.lock:
            return self._public(item)

    def _public(self, item):
        item = self.get(item['id'])
        result = {key: item.get(key) for key in ("id", "title", "workspace", "created", "updated", "pinned", "messages", "state", "trusted", "sessionId", "seq", "lastRunId", "modelOverride", "permissionModeOverride", "choice", "verification", "branch") } | {
            'imported': bool(item.get('importedConfigRoot')),
            "artifacts": list(item.get("artifacts", [])),
            "executions": list(item.get("executions", [])),
            "toolActivity": list(item.get("toolActivity", [])),
            "progress": self.progress_logs.metadata(item['id']),
            "requests": list(item.get("requests", {}).values())}
        connection = dict(item['connection']) if isinstance(item.get('connection'), dict) else None
        bridge = item.get('bridge')
        result['connectionStopped'] = bridge is None or getattr(bridge, 'cleanup_complete', False) is True
        live = bool(bridge and not bridge.closed)
        if connection is not None:
            if live and callable(getattr(bridge, 'model_state', None)):
                current = bridge.model_state()
                if isinstance(current, dict):
                    connection.update(current)
            capabilities = dict(connection.get('capabilities') or {})
            if live and isinstance(getattr(bridge, 'capabilities', None), dict):
                capabilities.update(bridge.capabilities)
            if not live:
                capabilities['setModel'] = False
                capabilities['setPermissionMode'] = False
                capabilities['setEffort'] = False
                connection['modelOverride'] = None
                connection['permissionModeOverride'] = None
                connection['effortOverride'] = None
            connection.update(capabilities=capabilities, connected=live, controlRestore=item.get('_controlRestore'))
        if not live:
            result['modelOverride'] = None
            result['permissionModeOverride'] = None
        result['connection'] = connection
        return result

    def bootstrap(self):
        with self.lock:
            sessions = self.session_order.ordered(self.visible_sessions())
            order = self.session_order.snapshot()
            order['ids'] = [sid for sid in order['ids'] if not self.session_visibility.contains(sid)]
            return {"application": "company-workspace", "version": self.info.get("version"), "error": self.error, "demo": self.demo,
                    "workspaceVersion": WORKSPACE_VERSION, "appRoot": str(Path(__file__).resolve().parents[1]), "historyWarning": self.history.warning,
                    "visibilityWarning": self.session_visibility.warning,
                    "runtime": runtime_context(self.command) if self.command and not self.demo else None,
                    "sessions": [{key: item.get(key) for key in ("id", "title", "workspace", "created", "updated", "pinned", "state")} |
                                 {"connectionState": 'live' if item.get('bridge') and not item['bridge'].closed else 'last-seen' if item.get('connection') else 'unavailable'} |
                                 {"artifactCount": item.get('_artifactCount', 0) if item.get('_historyUnloaded') else len(item.get('artifacts', []))} for item in sessions],
                    "sessionOrder": order,
                    "managedWorkspaceRoot": str(self.managed_workspace_root) if self.managed_workspace_root is not None else None,
                    "defaultWorkspace": str(self.default_workspace) if self.default_workspace is not None else '',
                    "workspaceLocationError": self.workspace_location_error,
                    "windowTitle": self.notifier.window_title,
                    "window": self.window_state(),
                    "upgradeProtocol": 1, "upgradeRestore": self.upgrade.restore,
                    "upgradeWarning": self.upgrade.warning,
                    "appUpdate": self.app_updates.snapshot(),
                    "appUpdateWarning": self.update_warning,
                    **self.shutdown_status()}

    def window_state(self):
        available = self.tray is not None and self.tray.available is True
        return {'traySupported': available, 'hideSupported': available and self.notifier.native_state['supported'],
                'reopenSupported': self._open_window_callback is not None,
                'mode': 'desktop' if self._desktop_window is not None else 'headless',
                'engine': 'WebView2' if self._desktop_window is not None else None,
                'closeBehavior': 'background' if available else 'quit' if self._desktop_window is not None else 'browser'}

    def hide_window(self):
        if not self.window_state()['hideSupported']:
            return {'hidden': False, 'supported': False, 'message': '트레이 연결을 확인하지 못했습니다. 현재 창에서 계속 사용해 주세요.'}
        self.notifier.bind()
        hidden = self.notifier.set_visible(False)
        return {'hidden': hidden, 'supported': True,
                'message': '트레이에서 계속 실행합니다.' if hidden else '앱 창을 확인하지 못했습니다. 창의 X를 눌러 닫아도 업무는 계속됩니다.'}

    def update_tray(self):
        if self.tray is not None:
            self.tray.update(running=sum(item['state'] in {'starting', 'running'} for item in self.visible_sessions()),
                             waiting=attention_snapshot(self.visible_sessions())['total'])

    def attention(self):
        with self.lock:
            from .desktop_notifications import notification_id
            pending = attention_snapshot(self.visible_sessions())
            for item in pending['items']:
                item['notificationId'] = notification_id(item['sessionId'], 'attention', item['id'])
            return {**pending,
                    'native': self.notifier.native_state, 'windowTitle': self.notifier.window_title,
                    'windowTheme': self.notifier.theme_state, 'desktop': {**self.desktop.snapshot(),
                        'nativeAvailable': bool((self._desktop_window and self._desktop_window.notification_available)
                                                or (self.tray and self.tray.available))},
                    'navigation': self._navigation,
                    'upgrade': self.upgrade.status()['upgrade'] if hasattr(self, 'upgrade') else None}

    def shutdown_status(self):
        with self._lifecycle:
            return {'closing': self._shutdown_state != 'running',
                    'closed': self._shutdown_state == 'closed',
                    'shutdownState': self._shutdown_state}

    @contextmanager
    def operation(self, *, upgrade_change=False):
        """Admit mutations before shutdown; drain accepted requests before close.

        The lifecycle condition is separate from the session lock so bootstrap
        remains available while a CLI connection is being stopped.
        """
        # Match the handoff commit barrier's lock order. Never retain either
        # lock while executing a request or waiting for CLI/process cleanup.
        with self.lock:
            with self._lifecycle:
                if self._shutdown_state != 'running':
                    raise AppClosing('앱을 종료하고 있습니다. 종료가 끝난 뒤 실행 아이콘으로 다시 열어 주세요.')
                self._active_operations += 1
                if upgrade_change:
                    self.upgrade.invalidate()
        try:
            yield
        finally:
            with self._lifecycle:
                self._active_operations -= 1
                self._lifecycle.notify_all()

    @staticmethod
    def clean_title(value):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100 or any(ord(c) < 32 for c in value):
            raise ValueError('업무 이름을 줄바꿈 없이 1~100자로 입력해 주세요.')
        return value.strip()

    def unique_title(self, value, exclude=None):
        """Caller holds the session lock; display names never change folder paths."""
        value = self.clean_title(value)
        key = lambda title: unicodedata.normalize('NFC', title).casefold()
        used = {key(row['title']) for row in self.visible_sessions() if row['id'] != exclude}
        if key(value) not in used:
            return value
        numbered = re.search(r' \(([2-9]|[1-9][0-9]+)\)$', value)
        base = value[:numbered.start()] if numbered else value
        start = int(numbered[1]) + 1 if numbered else 2
        for number in range(start, start + MAX_SESSIONS + 1):
            suffix = f' ({number})'
            candidate = base[:100-len(suffix)].rstrip() + suffix
            if key(candidate) not in used:
                return candidate
        raise ValueError('업무 이름을 구분할 수 없습니다. 다른 이름을 입력해 주세요.')

    def create(self, workspace, trusted, *, managed=False, title=None, managed_root=None):
        if trusted is not True:
            raise ValueError("이 폴더의 Claude 설정·후크·MCP 실행에 동의해 주세요.")
        if type(managed) is not bool:
            raise ValueError('새 업무 폴더 사용 여부를 확인해 주세요.')
        if managed_root is not None and (not managed or not isinstance(managed_root, str) or not managed_root
                                         or not Path(managed_root).is_absolute() or '..' in Path(managed_root).parts):
            raise ValueError('새 업무를 만들 저장 폴더를 선택해 주세요.')
        title = self.clean_title(title) if title is not None else '새 업무'
        with self.lock:
            if len(self.sessions) >= MAX_SESSIONS:
                raise ValueError('업무는 최대 500개까지 저장할 수 있습니다. 기존 업무를 이어서 사용해 주세요.')
            title = self.unique_title(title)
            sid = str(uuid.uuid4())
            if managed:
                # The displayed title is never interpreted as a filesystem path.
                if managed_root is not None:
                    base = folder(workspace_path(Path(managed_root)))
                    if self.demo and not base.is_relative_to(self.state.resolve(strict=True)):
                        raise ValueError('체험 모드에서는 체험용 저장 위치 안에만 새 업무를 만들 수 있습니다.')
                else:
                    if self.managed_workspace_root is None:
                        raise ValueError(self.workspace_location_error or DESKTOP_UNAVAILABLE)
                    base = workspace_path(self.managed_workspace_root.absolute())
                    if self._isolated_workspace_root:
                        base.mkdir(parents=True, exist_ok=True)
                    else:
                        # A disconnected/removed Desktop must not be silently recreated.
                        folder(self.default_workspace)
                        base.mkdir(exist_ok=True)
                base = workspace_path(base.resolve(strict=True))
                root = workspace_path(base / (time.strftime('%Y-%m-%d') + '-' + sid[:8]))
                if root.parent != base:
                    raise ValueError('새 업무 폴더 위치를 확인하지 못했습니다.')
                root.mkdir(exist_ok=False)
                root = root.resolve(strict=True)
            else:
                root = folder(workspace)
            now = time.time()
            item = {"id": sid, "workspace": str(root), "title": title, "created": now, "updated": now, "pinned": False,
                    "messages": [], "state": "idle", "seq": 0, "events": [], "requests": {},
                    "sessionId": None, "bridge": None, "trusted": True, "attachments": [], "artifacts": [], "lastRunId": None,
                    "modelOverride": None}
            self.sessions[sid] = item
            self.save(sid)
        return self.public(item)

    def update_session(self, sid, data):
        if not {'title', 'pinned'} & data.keys():
            raise ValueError('변경할 업무 이름 또는 고정 여부를 선택해 주세요.')
        title = self.clean_title(data['title']) if 'title' in data else None
        if 'pinned' in data and type(data['pinned']) is not bool:
            raise ValueError('업무 고정 여부를 확인해 주세요.')
        with self.lock:
            item = self.get(sid)
            if self.history.warning:
                raise ValueError(self.history.warning)
            previous = {key: item.get(key) for key in ('title', 'pinned', 'updated')}
            if title is not None:
                item['title'] = self.unique_title(title, sid)
            if 'pinned' in data:
                item['pinned'] = data['pinned']
            item['updated'] = time.time()
            try:
                self.save(sid)
            except (OSError, ValueError):
                item.update(previous)
                raise
            return self.public(item)

    def reorder_session(self, sid, target, position):
        with self.lock:
            if self.history.warning:
                raise ValueError(self.history.warning)
            return {'ok': True, 'sessionOrder': self.session_order.move(
                self.visible_sessions(), sid, target, position)}

    def visible_sessions(self):
        return [item for item in self.sessions.values() if not self.session_visibility.contains(item['id'])]

    def hide_session(self, sid, confirmed=False):
        """Remove a list entry only after proving no work would be orphaned."""
        if confirmed is not True:
            raise ValueError('업무 목록 삭제 안내를 확인해 주세요.')
        with self.lock:
            if self.session_visibility.contains(sid):
                return {'ok': True, 'hiddenId': sid, 'historyPreserved': True}
            item = self.get(sid)
            if self.session_visibility.warning:
                raise ValueError(self.session_visibility.warning)
            if (item.get('state') in {'starting', 'running', 'approval', 'question'}
                    or any(item.get(key) for key in ('_connecting', '_modelUpdating', '_dispatchClaim', '_choiceAnswerClaim'))
                    or item.get('requests') or item.get('choice')
                    or (item.get('verification') or {}).get('state') in {'checking', 'needs-review'}
                    or sid in self.dispatch.steering):
                raise ValueError('진행 중인 작업이나 답변·승인 대기를 먼저 마쳐 주세요. 자동으로 중지하지 않습니다.')
            pending = self.dispatch.queue.snapshot(sid)
            if self.dispatch.error or pending.get('warning'):
                raise ValueError('대기·예약 상태를 확인하지 못했습니다. 해당 상태를 확인한 뒤 목록에서 삭제해 주세요.')
            if any(row['status'] in {'queued', 'dispatching', 'submitted', 'needs_review'} for row in pending['queue']):
                raise ValueError('이어 할 일에 남은 요청을 먼저 완료하거나 취소해 주세요.')
            if any(row['enabled'] or row.get('pausedByUser') or row.get('nextRunAt') is not None
                   for row in pending['schedules']):
                raise ValueError('실행 예약을 먼저 취소해 주세요. 일시 정지한 예약도 유지되고 있습니다.')
            bridge = item.get('bridge')
            if bridge and (getattr(bridge, 'busy', False) or getattr(bridge, 'pending', None)
                           or getattr(bridge, 'stopping', False)
                           and getattr(bridge, 'cleanup_complete', False) is not True):
                raise ValueError('현재 연결의 작업 또는 종료가 끝난 뒤 목록에서 삭제해 주세요.')
            item['_removingFromList'] = True
        try:
            # Release only the idle CLI owned by this app, outside the session
            # lock so its final reader callbacks can finish normally.
            if bridge and getattr(bridge, 'cleanup_complete', False) is not True:
                if bridge.close() is not True or getattr(bridge, 'cleanup_complete', False) is not True:
                    raise ValueError('기존 연결의 종료를 확인하지 못해 목록을 유지했습니다. 잠시 뒤 다시 시도해 주세요.')
            with self.lock:
                # A final reader callback can still arrive during close(). If
                # it reports a decision, retain the task for explicit review.
                if (item.get('state') in {'starting', 'running', 'approval', 'question'}
                        or item.get('requests') or item.get('choice')
                        or (item.get('verification') or {}).get('state') in {'checking', 'needs-review'}):
                    raise ValueError('연결을 마무리하는 동안 확인할 내용이 도착해 목록을 유지했습니다. 업무 내용을 확인해 주세요.')
                self.session_visibility.set_hidden(sid, True)
                item['bridge'] = None
                item['trusted'] = False
                if self._navigation and self._navigation.get('sessionId') == sid:
                    self._navigation = None
                if self._viewed_session == sid:
                    self._viewed_session, self._viewed_until = None, 0
                self.update_tray()
                if hasattr(self, 'desktop'):
                    self.desktop.cancel_pending_session(sid)
                    self.desktop.retain_attention(attention_snapshot(self.visible_sessions())['items'])
                return {'ok': True, 'hiddenId': sid, 'historyPreserved': True}
        finally:
            with self.lock:
                item.pop('_removingFromList', None)

    def _finish_observation(self, item):
        before = item.pop('_artifactSnapshot', None)
        if before is None:
            return
        root = Path(item['workspace'])
        self.file_diffs.finish(root, item['lastRunId'])
        after = snapshot(root)
        found = changes(root, before, after, item['lastRunId'], time.time())
        item['artifacts'] = (item.get('artifacts', []) + found)[-MAX_ARTIFACTS:]
        item['artifactObservation'] = after.public() | {'limited': before.limited or after.limited,
                                                       'errors': before.errors + after.errors,
                                                       'retainedLimit': MAX_ARTIFACTS}

    def results(self, sid):
        with self.lock:
            item = self.get(sid)
            return {'artifacts': list(item.get('artifacts', [])), 'lastRunId': item.get('lastRunId'),
                    'observation': item.get('artifactObservation'), 'workspace': item['workspace']}

    def file_changes(self, sid, run_id=None, file_id=None):
        with self.lock:
            item = self.get(sid)
            root = workspace_folder(item)
            # Captures contain only files read after the task's trust decision.
            # Reviewing app-owned records after restart doesn't execute settings.
        if file_id is not None:
            return self.file_diffs.get(root, run_id, file_id)
        return self.file_diffs.list(root, run_id)

    def fork_session(self, sid, *, preview=False):
        from .conversation_fork import prepare_fork
        with self.lock:
            if self.demo or not self.command:
                raise ValueError('실제 Claude Code 대화를 연결한 뒤 분기할 수 있어요.')
            if self.history.warning:
                raise ValueError(self.history.warning)
            if len(self.sessions) >= MAX_SESSIONS and not preview:
                raise ValueError('업무는 최대 500개까지 저장할 수 있습니다.')
            parent = self.get(sid)
            workspace_folder(parent)
            root = runtime_context(self.command)['configRoot']
            item = prepare_fork(parent, self.info, root)
            if preview:
                return {'available': True, 'scope': 'latest',
                        'reason': '마지막 완료 기록에서 이어가요. 분기 생성만으로 AI 요청을 보내지는 않아요.'}
            item.update(bridge=None, requests={}, events=[], seq=0, state='idle',
                        attachments=[], artifacts=[], lastRunId=None)
            item['title'] = self.unique_title(item['title'][:100])
            self.sessions[item['id']] = item
            try:
                self.save(item['id'])
            except (OSError, ValueError):
                self.sessions.pop(item['id'], None)
                raise
            return {'ok': True, 'session': self.public(item)}

    def _resume_options(self, item):
        if 'branch' not in item:
            return {'resume': item.get('sessionId'),
                    **({'require_resume_identity': True} if item.get('importedConfigRoot') else {})}
        from .conversation_fork import branch_connection
        return branch_connection(item, self.info, runtime_context(self.command)['configRoot'])

    def reconnect(self, sid=None):
        if sid is not None:
            self.get(sid)
        if self.demo:
            return self.bootstrap() | {'ok': True, 'message': '화면 체험 모드입니다. 실제 AI 연결은 검사하지 않았습니다.'}
        if not self.reconnect_lock.acquire(blocking=False):
            raise ValueError('연결을 확인하고 있습니다. 잠시 후 결과를 확인해 주세요.')
        try:
            # Probe only --version/--help. Never stop an active child or replay a request.
            command = self.command if self._injected_command else resolve_cli()
            info = probe_cli(command)
            with self.lock:
                self.command, self.info, self.error = command, info, None
            return self.bootstrap() | {'ok': True, 'message': '실행 프로그램 연결을 확인했습니다. 로그인과 모델 응답은 다음 요청에서 확인합니다.'}
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            with self.lock:
                self.error = str(exc)
            raise ValueError(str(exc)) from exc
        finally:
            self.reconnect_lock.release()

    def set_model(self, sid, model):
        if model is not None and (not isinstance(model, str) or not model.strip() or len(model) > 160 or any(ord(c) < 32 for c in model)):
            raise ValueError('사용할 모델 이름을 확인해 주세요.')
        with self.lock:
            item = self.get(sid)
            if item['state'] in {'starting', 'running', 'question', 'approval'} or item.get('_modelUpdating') or item.get('_connecting') or item.get('_dispatchClaim'):
                raise ValueError('현재 업무를 마치거나 중지한 뒤 모델을 변경해 주세요.')
            bridge = item.get('bridge')
            if self.demo or not bridge or bridge.closed or not getattr(bridge, 'ready', threading.Event()).is_set():
                raise ValueError('업무를 한 번 실행해 연결한 뒤, 다음 요청부터 사용할 모델을 선택해 주세요.')
            item['_modelUpdating'] = True
            self._apply_control_baselines(item, bridge)
        try:
            # Bridge control responses may emit events: never hold the app lock while waiting.
            result = bridge.set_model(model.strip() if isinstance(model, str) else None)
            with self.lock:
                item['modelOverride'] = result.get('modelOverride')
                self._remember_control(item, 'model', result.get('modelOverride'))
            self._control_selected(item, bridge, 'model')
            with self.lock:
                return {'ok': True, 'modelOverride': item['modelOverride'], 'controlRestore': item.get('_controlRestore'), 'session': self.public(item)}
        finally:
            with self.lock:
                item['_modelUpdating'] = False

    def set_effort(self, sid, effort):
        if effort is not None and (not isinstance(effort, str) or not effort or len(effort) > 20):
            raise ValueError('추론 수준을 확인해 주세요.')
        with self.lock:
            item = self.get(sid)
            if item['state'] in {'starting', 'running', 'question', 'approval'} or item.get('_modelUpdating') or item.get('_connecting') or item.get('_dispatchClaim'):
                raise ValueError('현재 업무와 확인 요청이 끝난 뒤 추론 수준을 변경해 주세요.')
            bridge = item.get('bridge')
            if self.demo or not bridge or bridge.closed or not getattr(bridge, 'ready', threading.Event()).is_set():
                raise ValueError('업무 연결이 준비된 뒤 추론 수준을 선택해 주세요.')
            item['_modelUpdating'] = True
            self._apply_control_baselines(item, bridge)
        try:
            result = bridge.set_effort(effort)
            with self.lock:
                item.setdefault('connection', {}).update(result)
                self._remember_control(item, 'effort', result.get('effortOverride'))
            self._control_selected(item, bridge, 'effort')
            with self.lock:
                return {**bridge.model_state(), 'ok': True, 'controlRestore': item.get('_controlRestore'), 'session': self.public(item)}
        except (ValueError, OSError):
            with self.lock:
                self.emit(sid, 'effort_changed', bridge.model_state())
            raise
        finally:
            with self.lock:
                item['_modelUpdating'] = False

    def set_permission_mode(self, sid, mode, *, bypass_confirmed=False):
        if mode is not None and (not isinstance(mode, str) or not mode or len(mode) > 40):
            raise ValueError('승인 모드를 확인해 주세요.')
        if mode == 'bypassPermissions' and bypass_confirmed is not True:
            raise ValueError('Bypass는 파일 수정·명령 실행의 승인을 생략합니다. 위험 안내를 확인한 뒤 직접 선택해 주세요.')
        with self.lock:
            item = self.get(sid)
            if item['state'] in {'starting', 'running', 'question', 'approval'} or item.get('_modelUpdating') or item.get('_connecting') or item.get('_dispatchClaim'):
                raise ValueError('현재 업무와 확인 요청이 끝난 뒤 승인 모드를 변경해 주세요.')
            bridge = item.get('bridge')
            if self.demo or not bridge or bridge.closed or not getattr(bridge, 'ready', threading.Event()).is_set():
                raise ValueError('업무 연결이 준비된 뒤 승인 모드를 선택해 주세요.')
            item['_modelUpdating'] = True
            previous_state = item['state']
            self._apply_control_baselines(item, bridge)
        try:
            if mode == 'bypassPermissions' and not getattr(bridge, 'allow_bypass_permissions', False):
                if not bridge.model_state().get('bypassPermissions', {}).get('available'):
                    raise ValueError('현재 Claude 연결은 Bypass 선택을 지원하지 않습니다.')
                with self.lock:
                    item['bridge'] = None
                if bridge.close() is not True:
                    with self.lock:
                        item['bridge'] = bridge
                    raise ValueError('이전 연결의 종료를 확인하지 못했습니다. 현재 요청을 다시 보내지 않았습니다.')
                with self.lock:
                    root = workspace_folder(item)
                    self._check_import_context(item)
                    item['_allowBypass'] = True
                    resume_options = self._resume_options(item)
                    bridge = ClaudeSession(self.command, self.info, root,
                        lambda kind, data: self._emit_bridge(sid, bridge, kind, data), resume_options.pop('resume'), **resume_options,
                        allow_bypass_permissions=True)
                    item['bridge'] = bridge
                    item['_needsControlRestore'] = bool(item.get('_sessionControls'))
                    item['_pendingControlRestore'] = set(item.get('_sessionControls', {}))
                bridge.prepare()
                self._apply_control_baselines(item, bridge)
                self._restore_controls(item, bridge)
            result = bridge.set_permission_mode(mode)
            with self.lock:
                item.setdefault('connection', {}).update(result)
                item['permissionModeOverride'] = result.get('permissionModeOverride')
                self._remember_control(item, 'permissionMode', result.get('permissionMode') if mode is not None else None)
            self._control_selected(item, bridge, 'permissionMode')
            with self.lock:
                return {**bridge.model_state(), 'ok': True, 'controlRestore': item.get('_controlRestore'), 'session': self.public(item)}
        except (ValueError, OSError):
            with self.lock:
                state = bridge.model_state()
                item.setdefault('connection', {}).update(state)
                self.emit(sid, 'permission_mode_changed', state)
            raise
        finally:
            with self.lock:
                item['_modelUpdating'] = False
                if item['state'] not in {'error', 'running', 'question', 'approval'}:
                    item['state'] = previous_state

    def connection_capacity_available(self, item):
        """Call while holding self.lock; reservations and live bridges share slots."""
        if self.demo:
            return True
        bridge = item.get('bridge')
        if bridge is not None and not bridge.closed:
            return True
        occupied = sum(1 for row in self.sessions.values() if row is not item and (
            row.get('_dispatchClaim') or (row.get('bridge') and not row['bridge'].closed)))
        return occupied < 3

    def connect(self, sid, *, _dispatch_claim=None):
        """Explicitly prepare one trusted task's CLI; never send a prompt."""
        with self.lock:
            item = self.get(sid)
            if item.get('_dispatchClaim') != _dispatch_claim:
                raise ValueError('대기 요청을 전송하고 있습니다. 연결 준비는 잠시 후 다시 시도해 주세요.')
            self._check_import_context(item)
            if not item.get('trusted'):
                raise ValueError('명령을 불러오기 전에 이 업무 폴더의 설정·후크·MCP 실행에 동의해 주세요.')
            root = workspace_folder(item)
            if (item['state'] in {'starting', 'running', 'question', 'approval'}
                    or item.get('_modelUpdating') or item.get('_connecting')):
                raise ValueError('현재 업무와 연결 준비가 끝난 뒤 명령을 불러와 주세요.')
            if self.demo:
                raise ValueError('화면 체험에서는 실제 Claude 명령을 불러오지 않습니다.')
            if self.error:
                raise ValueError(self.error)
            bridge = item.get('bridge')
            if bridge is not None and bridge.closed and getattr(bridge, 'cleanup_complete', False) is not True:
                raise ValueError('이전 업무 연결의 종료를 확인한 뒤 다시 연결해 주세요.')
            if bridge is None or bridge.closed:
                self._capture_control_baselines(item, bridge)
                if not self.connection_capacity_available(item):
                    raise ValueError('연결된 대화가 3개입니다. 다른 대화의 연결을 중지한 뒤 다시 시도해 주세요.')
                resume_options = self._resume_options(item)
                bridge = ClaudeSession(self.command, self.info, root,
                    lambda kind, data: self._emit_bridge(sid, bridge, kind, data), resume_options.pop('resume'), **resume_options,
                    **({'allow_bypass_permissions': True} if item.get('_allowBypass') else {}))
                item['bridge'] = bridge
                item['modelOverride'] = None
                item['permissionModeOverride'] = None
                item.pop('connection', None)
                item['_needsControlRestore'] = bool(item.get('_sessionControls'))
                item['_pendingControlRestore'] = set(item.get('_sessionControls', {}))
                item['_controlRestoreIssues'] = {}
            item['_connecting'] = True
        try:
            # Reader events need the app lock. Never hold it while waiting for
            # initialization, and never create a synthetic conversation turn.
            bridge.prepare()
            self._apply_control_baselines(item, bridge)
            # Make the real, prepared state visible if a previously selected
            # mode is no longer available. No business request is sent until
            # every remembered selection has been acknowledged again.
            if item.get('_needsControlRestore'):
                self._emit_bridge(sid, bridge, 'connected', bridge.connection_state())
                self._restore_controls(item, bridge)
            with self.lock:
                if bridge.closed or bridge.stopping:
                    raise ValueError('명령 목록 준비가 중지되었습니다. 다시 연결해 주세요.')
                # Re-read after acquiring the lock so a richer system/init
                # received during prepare is never overwritten by an old copy.
                self.emit(sid, 'connected', bridge.connection_state())
                session = self.public(item)
                return {'ok': True, 'connection': session['connection'], 'session': session}
        finally:
            with self.lock:
                item['_connecting'] = False

    @staticmethod
    def _remember_control(item, name, value):
        # Current app lifetime only. HistoryStore deliberately excludes this
        # private field, so personal Claude settings and restart defaults stay
        # owned by the CLI.
        choices = item.setdefault('_sessionControls', {})
        # None is an explicit reset to the app-lifetime observed baseline, not
        # permission to inherit the last completed turn of a resumed process.
        choices[name] = value

    @staticmethod
    def _capture_control_baselines(item, bridge):
        if bridge is None:
            return
        baseline = item.setdefault('_controlBaselines', {})
        for name, attr in (('model', 'original_model'), ('permissionMode', 'original_permission_mode')):
            value = getattr(bridge, attr, None)
            if isinstance(value, str) and value:
                baseline.setdefault(name, value)
        efforts = baseline.setdefault('efforts', {})
        for model, value in getattr(bridge, '_effort_baselines', {}).items():
            efforts.setdefault(model, value)

    def _apply_control_baselines(self, item, bridge):
        with self.lock:
            baseline = item.get('_controlBaselines', {})
            for name, attr in (('model', 'original_model'), ('permissionMode', 'original_permission_mode')):
                if name in baseline:
                    setattr(bridge, attr, baseline[name])
            if hasattr(bridge, '_effort_baselines'):
                bridge._effort_baselines.update(baseline.get('efforts', {}))
            self._capture_control_baselines(item, bridge)

    def _publish_control_restore(self, item, bridge):
        with self.lock:
            if item.get('bridge') is not bridge:
                return
            issues = item.get('_controlRestoreIssues', {})
            actual = bridge.model_state()
            can_accept = getattr(bridge, 'can_accept_current_control', lambda name: False)
            state = {'status': 'needs_input', 'issues': [dict(issues[name],
                     canUseCurrent=can_accept(name), currentValue=actual.get(name)) for name in
                     ('model', 'effort', 'permissionMode') if name in issues], 'canSend': False} if issues else None
            changed = item.get('_controlRestore') != state
            item['_controlRestore'] = state
            item['_needsControlRestore'] = bool(item.get('_pendingControlRestore'))
            if changed:
                self.emit(item['id'], 'control_restore_changed', bridge.model_state())

    def accept_current_control(self, sid, control, action):
        if action != 'use_current' or control not in {'model', 'effort', 'permissionMode'}:
            raise ValueError('확인할 현재 CLI 설정을 선택해 주세요.')
        with self.lock:
            item = self.get(sid)
            if (item['state'] in {'starting', 'running', 'question', 'approval'} or item.get('_modelUpdating')
                    or item.get('_connecting') or item.get('_dispatchClaim')):
                raise ValueError('현재 업무와 연결 준비가 끝난 뒤 설정을 확인해 주세요.')
            bridge = item.get('bridge')
            if (not item.get('trusted') or not bridge or bridge.closed
                    or control not in item.get('_controlRestoreIssues', {})):
                raise ValueError('현재 연결에서 확인이 필요한 설정을 선택해 주세요.')
            item['_modelUpdating'] = True
        try:
            bridge.accept_current_control(control)
            with self.lock:
                # The user explicitly abandons this stale selection only. No
                # inherited value or other control becomes a new override.
                item.get('_sessionControls', {}).pop(control, None)
            self._control_selected(item, bridge, control)
            with self.lock:
                session = self.public(item)
                return {'ok': True, 'connection': session['connection'], 'session': session}
        finally:
            with self.lock:
                item['_modelUpdating'] = False

    def _control_selected(self, item, bridge, name):
        with self.lock:
            self._capture_control_baselines(item, bridge)
            pending = item.setdefault('_pendingControlRestore', set())
            pending.discard(name)
            item.setdefault('_controlRestoreIssues', {}).pop(name, None)
            # A new model ACK can change the advertised effort choices. Only
            # that dependent control is retried; unrelated rejected choices
            # remain pending until the user addresses them explicitly.
            if name == 'model' and 'effort' in item.get('_sessionControls', {}):
                pending.add('effort')
                item['_needsControlRestore'] = True
        if name == 'model':
            self._restore_controls(item, bridge, only={'effort'})
        self._publish_control_restore(item, bridge)

    def _restore_controls(self, item, bridge, *, only=None):
        with self.lock:
            if not item.get('_needsControlRestore'):
                return
            choices = dict(item.get('_sessionControls', {}))
            pending = item.setdefault('_pendingControlRestore', set(choices))
        actionable = {'model_invalid', 'model_rejected', 'model_unavailable',
                      'effort_invalid', 'effort_rejected', 'effort_reset_unavailable', 'effort_unavailable',
                      'permission_mode_invalid', 'permission_mode_rejected', 'permission_mode_unavailable',
                      'permission_mode_reset_unavailable', 'bypass_opt_in_required'}
        for name, apply in (('model', bridge.set_model), ('effort', bridge.set_effort),
                            ('permissionMode', bridge.set_permission_mode)):
            if name not in pending or name not in choices or (only is not None and name not in only):
                continue
            if name == 'effort' and 'model' in pending:
                continue
            try:
                apply(choices[name])
            except BridgeError as exc:
                process = getattr(bridge, 'process', None)
                if (exc.code not in actionable or bridge.closed or getattr(bridge, 'stopping', False)
                        or (process is not None and process.poll() is not None)):
                    raise
                with self.lock:
                    item.setdefault('_controlRestoreIssues', {})[name] = {
                        'control': name, 'code': exc.code, 'message': str(exc)}
            else:
                with self.lock:
                    pending.discard(name)
                    item.setdefault('_controlRestoreIssues', {}).pop(name, None)
                    self._capture_control_baselines(item, bridge)
        self._publish_control_restore(item, bridge)

    def _emit_bridge(self, sid, bridge, kind, data):
        # A retired child's delayed status/init must not change a replacement
        # connection for the same task.
        with self.lock:
            item = self.sessions.get(sid)
            if item is None or item.get('bridge') is not bridge:
                return
            if kind in {'tool_activity', 'progress_record'} and (not getattr(bridge, '_tool_activity_run_id', None)
                    or bridge._tool_activity_run_id != item.get('lastRunId')):
                return  # Connection preparation is not a submitted user turn.
            self.emit(sid, kind, data)

    def answer_choice(self, sid, choice_id, *, option_id=None, text=None):
        from .choices import answer
        with self.lock:
            item = self.get(sid)
            choice = item.get('choice')
            if not choice or not isinstance(choice_id, str) or choice['id'] != choice_id:
                raise ValueError('이미 답변했거나 만료된 선택 질문입니다.')
            if item['state'] not in {'idle', 'done'}:
                raise ValueError('현재 요청이 끝난 뒤 선택해 주세요.')
            if not item.get('trusted'):
                raise ValueError('업무 저장 위치를 다시 확인한 뒤 선택해 주세요.')
            if item.get('_choiceAnswerClaim'):
                raise ValueError('이 선택 질문의 답변을 처리하고 있습니다.')
            value = answer(choice, option_id=option_id, custom=text)
            claim = (choice_id, secrets.token_hex(16))
            item['_choiceAnswerClaim'] = claim
        try:
            # Reconnecting may wait for reader-thread ACKs. The app lock must
            # be released; the claim still prevents duplicate answer/normal sends.
            self.send(sid, value, [], _choice_claim=claim)
            with self.lock:
                return {'ok': True, 'session': self.public(item)}
        finally:
            with self.lock:
                if item.get('_choiceAnswerClaim') == claim:
                    item.pop('_choiceAnswerClaim', None)

    def emit(self, sid, kind, data):
        with self.lock:
            if self.session_visibility.contains(sid):
                return
            item = self.get(sid, _internal=True)
            if kind == 'progress_record':
                # Never relabel a late callback as the new request, and never
                # retain detail in the normal history/event mirror.
                if (not isinstance(data, dict) or not item.get('lastRunId')
                        or data.get('runId') != item['lastRunId']
                        or item.get('state') not in {'starting', 'running', 'approval', 'question'}):
                    return
                metadata = self.progress_logs.append(sid, data)
                if metadata is None:
                    return
                item['seq'] += 1
                # One pending metadata notification is enough. Coalescing keeps
                # high-volume tools from evicting approvals or terminal events.
                item['events'] = [event for event in item['events'] if event['type'] != 'progress_changed']
                item['events'].append({'seq': item['seq'], 'type': 'progress_changed',
                                       'data': {'runId': item['lastRunId'], 'progress': metadata}})
                item['events'] = item['events'][-300:]
                return
            if kind in {'request', 'choice'} or kind == 'status' and data.get('state') in {'starting', 'running'}:
                self.upgrade.invalidate()
            if kind in {'connected', 'model_changed', 'effort_changed', 'permission_mode_changed', 'control_restore_changed'}:
                data = dict(data, controlRestore=item.get('_controlRestore'))
            if kind in {'assistant', 'assistant_delta', 'queued_user'}:
                data = dict(data, runId=item.get('lastRunId'))
            if kind == 'status' and data.get('state') in {'starting', 'running'} and item.get('lastRunId'):
                data = dict(data, runId=item['lastRunId'])
            if kind == 'tool_activity':
                from .tool_activity import merge_activity, normalize_activity
                # Capture callbacks retain their originating turn. Never relabel
                # a late result or replay as evidence for a newer user request.
                if (not item.get('lastRunId') or not isinstance(data, dict)
                        or data.get('runId') is not None and data.get('runId') != item.get('lastRunId')):
                    return
                clean = normalize_activity(data, run_id=item.get('lastRunId'))
                if clean is None:
                    return
                before = item.get('toolActivity', [])
                item['toolActivity'] = merge_activity(before, clean)
                if item['toolActivity'] == before:
                    return
                data = next((row for row in item['toolActivity'] if row['id'] == clean['id'] and row.get('runId') == clean.get('runId')), None)
                if data is None:
                    return
            if kind == 'execution':
                from .executions import merge_execution, normalize_execution
                clean = normalize_execution(data, run_id=item.get('lastRunId'))
                if clean is None:
                    return
                item['executions'] = merge_execution(item.get('executions', []), clean)
                data = next((row for row in item['executions'] if row['id'] == clean['id'] and row.get('runId') == clean.get('runId')), None)
                if data is None:
                    return
            if kind == "assistant":
                item["messages"].append({"role": "assistant", "text": str(data["text"])[:100000], 'runId':item.get('lastRunId')})
                item["messages"] = item["messages"][-150:]
                # Keep the UI mirror bounded; the CLI owns the full transcript.
                while len(item["messages"]) > 1 and sum(len(row.get("text", "")) for row in item["messages"]) > 500000:
                    item["messages"].pop(0)
            elif kind == 'choice':
                from .choices import normalize
                clean = normalize(data)
                if item.get('_choiceSourceKey') == clean['id']:
                    return
                item['_choiceSourceKey'] = clean['id']
                data = {**clean, 'id': secrets.token_hex(16)}
                item['choice'] = data
            elif kind == 'choice_closed':
                item.pop('choice', None)
            elif kind == 'verification':
                item['verification'] = data
            elif kind == "request":
                previous = item['requests'].get(data['id'])
                data = {**data, '_attentionId': previous['_attentionId'] if previous and previous.get('_attentionId') else secrets.token_hex(16)}
                item["requests"][data["id"]] = data
                item["state"] = "question" if data["tool"] == "AskUserQuestion" else "approval"
            elif kind == "request_closed":
                item["requests"].pop(data["id"], None)
                # A fast CLI can finish before the HTTP approval handler returns.
                # Closing that old question must not turn a completed task into
                # an endless spinner (nor hide another pending question).
                if item['state'] in {'starting', 'running', 'question', 'approval'}:
                    remaining = list(item['requests'].values())
                    item['state'] = ('question' if remaining[-1]['tool'] == 'AskUserQuestion' else 'approval') if remaining else 'running'
                data['state'] = item['state']
            elif kind == "status":
                item["state"] = data["state"]
                if data["state"] == "stopped":
                    item["requests"].clear()
                    item.pop('choice', None)
                    item['modelOverride'] = None
                    item['permissionModeOverride'] = None
                    if isinstance(item.get('connection'), dict):
                        item['connection'] = self.public(item)['connection']
                        data['connection'] = item['connection']
            elif kind == "connected":
                item["connection"] = data
                item["sessionId"] = data.get("sessionId")
                item['modelOverride'] = data.get('modelOverride')
                item['permissionModeOverride'] = data.get('permissionModeOverride')
            elif kind == "model_changed":
                item.setdefault('connection', {}).update(data)
                item['modelOverride'] = data.get('modelOverride')
            elif kind == 'permission_mode_changed':
                item.setdefault('connection', {}).update(data)
                item['permissionModeOverride'] = data.get('permissionModeOverride')
            elif kind in {'effort_changed', 'control_restore_changed'}:
                item.setdefault('connection', {}).update(data)
                item['modelOverride'] = data.get('modelOverride')
                item['permissionModeOverride'] = data.get('permissionModeOverride')
            elif kind == "result":
                item["sessionId"] = data.get("sessionId")
                item["state"] = "done"
                if item.get('branch') and item['sessionId'] == item['branch']['childSessionId']:
                    item['branch']['status'] = 'active'
                    data['branch'] = dict(item['branch'])
                item['verification'] = data.get('verification')
            elif kind == "error":
                item["state"] = "error"
                item["requests"].clear()
                item.pop('choice', None)
                if "resumeSessionId" in data:
                    item["sessionId"] = data["resumeSessionId"]
            terminal = kind in {'result', 'error'} or kind == 'status' and data.get('state') == 'stopped'
            if terminal:
                from .executions import normalize_executions
                from .tool_activity import normalize_activities
                item['executions'] = normalize_executions(item.get('executions', []), interrupted=True)
                item['toolActivity'] = normalize_activities(item.get('toolActivity', []), interrupted=True)
                data['toolActivity'] = list(item['toolActivity'])
                self._finish_observation(item)
                data['artifacts'] = list(item.get('artifacts', []))
                data['lastRunId'] = item.get('lastRunId')
            if kind in {'assistant', 'connected', 'result', 'error', 'model_changed', 'choice', 'permission_mode_changed'} or terminal:
                item['updated'] = time.time()
            item["seq"] += 1
            item["events"].append({"seq": item["seq"], "type": kind, "data": data})
            item["events"] = item["events"][-300:]
            if kind in {"assistant", "connected", "result", "error", "model_changed", 'choice', 'permission_mode_changed', 'execution', 'tool_activity'} or terminal:
                self.save(sid)
            if kind in {'request', 'request_closed', 'choice', 'choice_closed', 'status', 'result', 'error'}:
                pending = attention_snapshot(self.visible_sessions())
                self.notifier.update(pending)
                self.update_tray()
                if hasattr(self, 'desktop'):
                    self.desktop.retain_attention(pending['items'])
                    for notice in pending['items']:
                        self._publish_notification(notice['sessionId'], notice['title'], 'attention', notice['id'])
                    if (kind in {'result', 'error'} and item.get('lastRunId') and not item.get('_modelUpdating') and not item.get('_connecting')
                            and not (kind == 'result' and any(row['sessionId'] == sid for row in pending['items']))):
                        notification_kind = 'error' if kind == 'error' or (data.get('verification') or {}).get('state') == 'needs-review' else 'completed'
                        self._publish_notification(sid, item['title'], notification_kind,
                                             kind + ':' + item['lastRunId'])
            if hasattr(self, 'dispatch'):
                self.dispatch.observe(sid, kind, data)

    @staticmethod
    def _validate_choice_claim(item, claim):
        pending = item.get('_choiceAnswerClaim')
        if pending is not None and pending != claim:
            raise ValueError('선택 질문의 답변을 처리한 뒤 다음 요청을 보내 주세요.')
        if claim is not None and (pending != claim or item.get('choice', {}).get('id') != claim[0]):
            raise ValueError('이미 답변했거나 만료된 선택 질문입니다.')

    @staticmethod
    def validate_attachments(attachments):
        if not isinstance(attachments, list) or len(attachments) > 12:
            raise ValueError('파일은 한 번에 12개까지 선택할 수 있습니다.')
        paths = []
        for value in attachments:
            if not isinstance(value, str) or not value:
                raise ValueError('첨부 파일의 전체 경로를 확인해 주세요.')
            candidate = Path(value).expanduser()
            if not candidate.is_absolute() or '..' in candidate.parts:
                raise ValueError('첨부 파일의 전체 경로를 확인해 주세요.')
            path = workspace_path(candidate).resolve(strict=True)
            if not path.is_file() or path.suffix.lower() not in REFERENCE_FILE_TYPES:
                raise ValueError('문서·이미지 또는 소스 파일을 선택해 주세요.')
            paths.append(str(path))
        return list(dict.fromkeys(paths))

    def send(self, sid, text, attachments, trusted=False, *, _choice_claim=None, _dispatch_claim=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 32000:
            raise ValueError("요청은 1~32,000자로 입력해 주세요.")
        if not isinstance(attachments, list) or len(attachments) > 12:
            raise ValueError("파일은 한 번에 12개까지 선택할 수 있습니다.")
        with self.lock:
            current = self.get(sid)
            if current.get('_dispatchClaim') != _dispatch_claim:
                raise ValueError('대기 요청을 전송하고 있습니다. 잠시 후 다시 보내 주세요.')
            if sid in self.dispatch.steering or (getattr(current.get('bridge'), 'stopping', False) is True
                    and getattr(current.get('bridge'), 'cleanup_complete', False) is not True):
                raise ValueError('현재 작업을 중지하고 있습니다. 중지가 끝난 뒤 이어서 보내 주세요.')
            self._validate_choice_claim(current, _choice_claim)
            old_bridge = current.get('bridge')
            if current.get('_controlRestore') and old_bridge is not None and not old_bridge.closed:
                raise ControlRestoreRequired(connection=self.public(current)['connection'])
            restore = (not self.demo and bool(current.get('_sessionControls'))
                       and (old_bridge is None or old_bridge.closed or current.get('_needsControlRestore')))
        if restore:
            self.connect(sid, _dispatch_claim=_dispatch_claim)
        with self.lock:
            item = self.get(sid)
            if item.get('_controlRestore'):
                raise ControlRestoreRequired(connection=self.public(item)['connection'])
            if item.get('_dispatchClaim') != _dispatch_claim:
                raise ValueError('대기 요청을 전송하고 있습니다. 잠시 후 다시 보내 주세요.')
            if sid in self.dispatch.steering or (getattr(item.get('bridge'), 'stopping', False) is True
                    and getattr(item.get('bridge'), 'cleanup_complete', False) is not True):
                raise ValueError('현재 작업을 중지하고 있습니다. 중지가 끝난 뒤 이어서 보내 주세요.')
            self._validate_choice_claim(item, _choice_claim)
            if not item.get("trusted") and trusted is not True:
                raise ValueError("다시 시작하기 전에 작업 폴더의 설정 실행에 동의해 주세요.")
            workspace_folder(item)
            self._check_import_context(item)
            if item["state"] in {"starting", "running", "question", "approval"} or item.get('_modelUpdating') or item.get('_connecting'):
                raise ValueError("현재 진행 중인 작업을 먼저 마치거나 중지해 주세요.")
            if item.get('branch', {}).get('status') == 'pending':
                self._resume_options(item)  # Recheck even after control-only preparation.
            paths = self.validate_attachments(attachments)
            if self.error:
                raise ValueError(self.error)
            if not self.demo:
                bridge = item.get("bridge")
                if bridge is not None and bridge.closed and getattr(bridge, 'cleanup_complete', False) is not True:
                    raise ValueError('이전 업무 연결의 종료를 확인한 뒤 다시 요청해 주세요.')
                if bridge is None or bridge.closed:
                    if not self.connection_capacity_available(item):
                        raise ValueError("연결된 대화가 3개입니다. 다른 대화의 연결을 중지한 뒤 다시 시도해 주세요.")
                    resume_options = self._resume_options(item)
                    bridge = ClaudeSession(self.command, self.info, Path(item["workspace"]),
                                           lambda kind, data: self._emit_bridge(sid, bridge, kind, data), resume_options.pop('resume'), **resume_options,
                                           **({'allow_bypass_permissions': True} if item.get('_allowBypass') else {}))
                    item["bridge"] = bridge
                    item['modelOverride'] = None
                    item.pop('connection', None)  # no stale init evidence during reconnect
            if item.get('choice'):
                self.emit(sid, 'choice_closed', {'id': item['choice']['id']})
            item.pop('_choiceSourceKey', None)
            item.pop('verification', None)
            item["trusted"] = True
            item["attachments"] = list(dict.fromkeys(item.get("attachments", []) + paths))
            if not item['messages'] and re.fullmatch(r'새 업무(?: \((?:[2-9]|[1-9][0-9]+)\))?', item['title']):
                item["title"] = self.unique_title(text.strip().splitlines()[0][:35], sid)
            item["messages"].append({"role": "user", "text": text.strip(), "files": paths})
            if _dispatch_claim is not None:
                item['messages'][-1]['requestId'] = _dispatch_claim
            item["state"] = "starting"
            item['updated'] = time.time()
            item['lastRunId'] = uuid.uuid4().hex
            item['messages'][-1]['runId'] = item['lastRunId']
            item['_artifactSnapshot'] = snapshot(Path(item['workspace']))
            self.file_diffs.start(Path(item['workspace']), item['lastRunId'])
            item['artifactObservation'] = item['_artifactSnapshot'].public()
            self.save(sid)
            if _dispatch_claim is not None:
                self.emit(sid, 'queued_user', {'text': text.strip(), 'files': paths, 'requestId': _dispatch_claim})
            # No copied files, skill hardcode, auxiliary inference or rewritten user intent.
            prompt = text.strip()
            if paths:
                prompt += "\n\n사용자가 선택한 원본 파일 경로(JSON):\n" + json.dumps(paths, ensure_ascii=False)
            if self.demo:
                from .demo import run
                threading.Thread(target=run, args=(self, sid, text), daemon=True).start()
            else:
                try:
                    bridge._tool_activity_run_id = item['lastRunId']
                    bridge.send(prompt)
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    self.emit(sid, 'error', {'message': str(exc), 'code': 'send_failed'})
                    raise

    def respond(self, sid, rid, allow, answers=None, permission_choice_id=None):
        if type(allow) is not bool:
            raise ValueError("승인 또는 거절을 선택해 주세요.")
        if self.demo:
            from .demo import respond
            respond(self, sid, rid, allow, answers)
        else:
            bridge = self.get(sid).get("bridge")
            if not bridge:
                raise ValueError("연결이 종료된 질문입니다.")
            if permission_choice_id is None:
                bridge.respond(rid, allow, answers)
            else:
                bridge.respond(rid, allow, answers, permission_choice_id=permission_choice_id)

    def stop(self, sid):
        self.dispatch.manual_stop(sid)
        bridge = self.get(sid).get("bridge")
        if bridge:
            bridge.interrupt()
        else:
            self.emit(sid, "status", {"state": "stopped", "label": "중지했어요"})

    def allowed_file(self, sid, value):
        item = self.get(sid)
        if not isinstance(value, (str, Path)) or not str(value) or len(str(value)) > 32767:
            raise ValueError('파일 경로를 확인해 주세요.')
        candidate = Path(value).expanduser()
        if not candidate.is_absolute() or '..' in candidate.parts:
            raise ValueError('파일의 전체 경로를 확인해 주세요.')
        path = workspace_path(candidate).resolve(strict=True)
        root = workspace_folder(item)
        if not path.is_file() or path.suffix.lower() not in SAFE_FILES:
            raise ValueError("미리보기를 지원하지 않는 파일입니다.")
        if not path.is_relative_to(root) and str(path) not in item.get("attachments", []):
            raise ValueError("이 대화의 작업 폴더 또는 직접 선택한 파일만 열 수 있습니다.")
        return path

    def files(self, sid):
        root = workspace_folder(self.get(sid))
        observed = snapshot(root, max_files=100)
        return [{"name": str(Path(value).relative_to(root)), "path": value, "size": signature[1]}
                for value, signature in observed.files.items()]

    def pick(self, kind, initial_directory=None):
        if os.name != "nt":
            raise ValueError("이 버전의 파일 선택 창은 Windows에서 지원합니다. 경로를 입력해 주세요.")
        if kind not in {"folder", "files"}:
            raise ValueError("올바른 선택 종류가 아닙니다.")
        if initial_directory is not None:
            if not isinstance(initial_directory, str) or not initial_directory or not Path(initial_directory).is_absolute():
                raise ValueError('선택 창을 시작할 폴더를 확인해 주세요.')
            initial_directory = str(folder(initial_directory))
        if not self.dialog_lock.acquire(blocking=False):
            raise ValueError("이미 열린 파일 선택 창을 먼저 닫아 주세요.")
        try:
            owner = workspace_window_handle()
            with private_picker_directory() as directory:
                result_path = Path(directory) / 'result.json'
                args = [powershell_path(), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-File",
                        str(Path(__file__).parent / "Pick-Path.ps1"), "-Kind", kind,
                        "-OwnerHandle", str(owner), "-ResultPath", str(result_path)]
                if initial_directory is not None:
                    args.extend(['-InitialDirectory', initial_directory])
                try:
                    # Console streams are diagnostics, never part of the response.
                    result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            timeout=300, creationflags=HIDDEN)
                except subprocess.TimeoutExpired as exc:
                    raise ValueError('선택 시간이 지나 창을 닫았습니다. 파일 선택 창을 다시 열어 주세요.') from exc
                response = read_picker_result(result_path, kind)
                if result.returncode:
                    raise ValueError('파일 선택 창을 열지 못했습니다. 경로를 직접 입력해 주세요.')
                return response
        finally:
            self.dialog_lock.release()

    def close(self):
        if hasattr(self, 'app_updates'):
            self.app_updates.close()
        if hasattr(self, 'dispatch'):
            self.dispatch.stop()
        self.notifier.close()
        if hasattr(self, 'desktop'):
            self.desktop.close()
        # Close admission immediately, including when another quit owns cleanup.
        with self._lifecycle:
            if self._shutdown_state in {'running', 'failed'}:
                self._shutdown_state = 'closing'
            while self._active_operations:
                self._lifecycle.wait()
        with self._close_lock:
            with self._lifecycle:
                if self._shutdown_state == 'closed':
                    return True
            with self.lock:
                bridges = [item['bridge'] for item in self.sessions.values() if item.get('bridge')]
            succeeded = True
            for bridge in bridges:
                try:
                    # ClaudeSession.close returns explicit process-cleanup evidence.
                    # A false result must not be replaced by a successful UI message.
                    if bridge.close() is not True:
                        succeeded = False
                except Exception:
                    succeeded = False
            with self._lifecycle:
                self._shutdown_state = 'closed' if succeeded else 'failed'
                self._lifecycle.notify_all()
            return succeeded


class AppClosing(ValueError):
    pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    # A fresh WebView opens many asset connections before desktop initialization
    # yields to serve_forever. Python 3.11's default backlog of five can drop
    # those connections before Handler sees them. This queues sockets, not
    # worker threads, and keeps the existing loopback-only listener unchanged.
    request_queue_size = 64

    def __init__(self, app, port=0):
        self.app = app
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = "http://127.0.0.1:" + str(self.server_port)


class Handler(BaseHTTPRequestHandler):
    server_version = "CompanyWorkspace"

    def log_message(self, *args):
        pass  # URLs, auth tokens and file paths must never enter access logs.

    def reply(self, data, status=200, content_type="application/json; charset=utf-8", *, location=None, csp=None):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if location is not None:
            self.send_header("Location", location)
        self.send_header("Content-Security-Policy", csp if csp is not None else "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self' about:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def valid_request(self, auth=True):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        return not auth or hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.server.app.token)

    def do_GET(self):
        route = urlsplit(self.path)
        if not self.valid_request(auth=route.path.startswith("/api/")):
            return self.reply({"error": "이 창의 연결 권한을 확인할 수 없습니다. 실행 아이콘으로 다시 열어 주세요."}, 403)
        try:
            app = self.server.app
            query = parse_qs(route.query)
            sid = query.get("id", [""])[0]
            if route.path == "/api/bootstrap":
                return self.reply(app.bootstrap())
            if route.path == '/api/app-update':
                return self.reply(app.app_updates.check())
            if route.path == '/api/attention':
                return self.reply(app.attention())
            if route.path == '/api/upgrade':
                return self.reply(app.upgrade.status(query.get('requestId', [None])[0]))
            if route.path == '/api/claude-sessions':
                return self.reply(app.import_sessions(query.get('sessionId', [None])[0]))
            if route.path == '/api/dispatch':
                return self.reply(app.dispatch.snapshot(sid))
            if route.path == "/api/events":
                after = int(query.get("after", ["0"])[0])
                deadline = time.monotonic() + 20
                while True:
                    with app.lock:
                        item = app.get(sid)
                        events = [row for row in item["events"] if row["seq"] > after]
                        if events or time.monotonic() >= deadline:
                            # Full bounded messages on recovery only, otherwise deltas.
                            return self.reply({"events": events, "state": item["state"], "seq": item["seq"]})
                    time.sleep(.15)
            if route.path == "/api/session":
                return self.reply(app.public(app.get(sid)))
            if route.path == '/api/progress':
                with app.lock:
                    app.get(sid)  # Same visibility/session boundary as /api/session.
                    return self.reply(app.progress_logs.page(sid,
                        before=query.get('before', [None])[0], limit=query.get('limit', ['50'])[0],
                        run_id=query.get('runId', [None])[0]))
            if route.path == "/api/capabilities":
                with app.lock:
                    selected = app.public(app.get(sid)) if sid else None
                # Older clients retain the task-bound view. The explicit scope
                # is a read-only browsing context, never an execution/trust change.
                if 'scope' in query:
                    scope = query['scope'][0]
                    if scope not in {'common', 'folder'}:
                        raise ValueError('목록 조회 범위를 확인해 주세요.')
                    folder = None
                    if scope == 'folder':
                        raw_folder = query.get('workspace', [''])[0]
                        if not raw_folder or len(raw_folder) > 32767:
                            raise ValueError('목록을 확인할 폴더를 선택해 주세요.')
                        candidate = Path(raw_folder)
                        if not candidate.is_absolute():
                            raise ValueError('목록을 확인할 폴더는 전체 경로로 선택해 주세요.')
                        folder = workspace_path(candidate).resolve(strict=True)
                        if not folder.is_dir():
                            raise ValueError('목록을 확인할 실제 폴더를 선택해 주세요.')
                        if selected and Path(selected['workspace']).resolve(strict=True) != folder:
                            raise ValueError('선택한 목록 폴더와 연결 업무의 폴더가 다릅니다.')
                    elif 'workspace' in query:
                        raise ValueError('공통 설치 스킬에는 폴더를 지정하지 않습니다. 업무는 연결 목록의 출처로만 사용됩니다.')
                    return self.reply(catalog(selected, client=app.inventory_client,
                                              demo=app.demo, scope=scope,
                                              workspace=str(folder) if folder else None))
                return self.reply(catalog(selected, client=app.inventory_client,
                                          demo=app.demo, validate_workspace=workspace_folder))
            if route.path == "/api/files":
                return self.reply({"files": app.files(sid)})
            if route.path == '/api/changes':
                return self.reply(app.file_changes(sid, query.get('run', [None])[0]))
            if route.path == '/api/changes/file':
                return self.reply(app.file_changes(sid, query.get('run', [''])[0], query.get('file', [''])[0]))
            if route.path == '/api/session/branch':
                return self.reply(app.fork_session(sid, preview=True))
            if route.path == "/api/results":
                return self.reply(app.results(sid))
            if route.path == "/api/preview":
                path = app.allowed_file(sid, query.get("path", [""])[0])
                if not source_preview_allowed(path, workspace_folder(app.get(sid))):
                    raise ValueError('숨김 설정 또는 인증 정보로 보이는 소스 파일은 미리보기에서 제외합니다.')
                preview = build_preview(path)
                if preview['kind'] != 'external' and app.get(sid).get('observation'):
                    app.get(sid)['observation']['previewed'].add(str(path))
                return self.reply(preview)
            manual_path = unquote(route.path)
            if manual_path in {'/manual/guide', '/manual/' + MANUAL_FILENAME}:
                return self.reply((ASSETS.parent.parent / 'docs' / MANUAL_FILENAME).read_bytes(),
                                  content_type='text/html; charset=utf-8', csp=MANUAL_CSP)
            if manual_path in MANUAL_ALIASES:
                return self.reply({}, 302, location='/manual/guide' + MANUAL_ALIASES[manual_path])
            assets = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/path-picker.js": ("path-picker.js", "text/javascript; charset=utf-8"),
                      "/path-picker.css": ("path-picker.css", "text/css; charset=utf-8"),
                      "/composer.js": ("composer.js", "text/javascript; charset=utf-8"),
                      "/inline-controls.js": ("inline-controls.js", "text/javascript; charset=utf-8"),
                      "/input-keys.js": ("input-keys.js", "text/javascript; charset=utf-8"),
                      "/chat-shortcuts.js": ("chat-shortcuts.js", "text/javascript; charset=utf-8"),
                      "/chat-shortcuts.css": ("chat-shortcuts.css", "text/css; charset=utf-8"),
                      "/attention.js": ("attention.js", "text/javascript; charset=utf-8"),
                      "/desktop.js": ("desktop.js", "text/javascript; charset=utf-8"),
                      "/session-import.js": ("session-import.js", "text/javascript; charset=utf-8"),
                      "/rich-content.js": ("rich-content.js", "text/javascript; charset=utf-8"),
                      "/startup-health.js": ("startup-health.js", "text/javascript; charset=utf-8"),
                      "/startup-health.css": ("startup-health.css", "text/css; charset=utf-8"),
                      "/execution-view.js": ("execution-view.js", "text/javascript; charset=utf-8"),
                      "/rich-content.css": ("rich-content.css", "text/css; charset=utf-8"),
                      "/execution-view.css": ("execution-view.css", "text/css; charset=utf-8"),
                      "/tool-activity.js": ("tool-activity.js", "text/javascript; charset=utf-8"),
                      "/tool-activity.css": ("tool-activity.css", "text/css; charset=utf-8"),
                      "/progress-view.js": ("progress-view.js", "text/javascript; charset=utf-8"),
                      "/progress-view.css": ("progress-view.css", "text/css; charset=utf-8"),
                      "/upgrade-handoff.js": ("upgrade-handoff.js", "text/javascript; charset=utf-8"),
                      "/upgrade-handoff.css": ("upgrade-handoff.css", "text/css; charset=utf-8"),
                      "/app-updates.js": ("app-updates.js", "text/javascript; charset=utf-8"),
                      "/app-updates.css": ("app-updates.css", "text/css; charset=utf-8"),
                      "/rendering.js": ("rendering.js", "text/javascript; charset=utf-8"),
                      "/attachments.js": ("attachments.js", "text/javascript; charset=utf-8"),
                      "/workflow.js": ("workflow.js", "text/javascript; charset=utf-8"),
                      "/capabilities.js": ("capabilities.js", "text/javascript; charset=utf-8"),
                      "/productivity.js": ("productivity.js", "text/javascript; charset=utf-8"),
                      "/palette.js": ("palette.js", "text/javascript; charset=utf-8"),
                      "/layout.js": ("layout.js", "text/javascript; charset=utf-8"),
                      "/productivity.css": ("productivity.css", "text/css; charset=utf-8"),
                      "/review.css": ("review.css", "text/css; charset=utf-8"),
                      "/app.css": ("app.css", "text/css; charset=utf-8"),
                      "/fonts/NotoSansKR-Variable.woff": ("fonts/NotoSansKR-Variable.woff", "font/woff"),
                      "/favicon.ico": ("app-icon.ico", "image/vnd.microsoft.icon"),
                      "/icon.svg": ("icon.svg", "image/svg+xml"),
                      "/app-icon-192.png": ("app-icon-192.png", "image/png"),
                      "/app-icon-512.png": ("app-icon-512.png", "image/png")}
            if route.path in assets:
                name, mime = assets[route.path]
                return self.reply((ASSETS / name).read_bytes(), content_type=mime)
            return self.reply({"error": "없는 화면입니다."}, 404)
        except (ValueError, OSError, KeyError) as exc:
            return self.reply({"error": str(exc)}, 400)

    def do_POST(self):
        if urlsplit(self.path).path == '/api/attachments/upload':
            return self.upload_attachment()
        if not self.valid_request() or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            # Closing a socket with an unread POST body can reset the response
            # on Windows. Discard only a small, declared body; never parse it or
            # execute the rejected operation, and never wait indefinitely.
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if 0 < length <= MAX_BODY:
                    self.connection.settimeout(.5)
                    self.rfile.read(length)
            except (ValueError, OSError):
                pass
            self.close_connection = True
            return self.reply({"error": "허용되지 않은 요청입니다."}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return self.reply({"error": "요청 크기가 너무 큽니다."}, 413)
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("올바른 요청 형식이 아닙니다.")
            app = self.server.app
            route = urlsplit(self.path).path
            if route == '/api/upgrade':
                result = app.upgrade.action(data)
                if result.get('closed') is True:
                    try:
                        return self.reply(result)
                    finally:
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
                status = 503 if result.get('state') == 'failed' else 409 if not result.get('ok') else 200
                return self.reply(result, status)
            if route == "/api/quit":
                if not app.close():
                    return self.reply({'ok': False, **app.shutdown_status(),
                                       'error': 'CLI 연결의 종료를 확인하지 못했습니다. 진행 중인 작업을 확인한 뒤 다시 시도해 주세요.'}, 503)
                try:
                    return self.reply({'ok': True, **app.shutdown_status()})
                finally:
                    # Deliver completion only after cleanup, then stop accepting HTTP.
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
            read_only = route in {'/api/ui-health', '/api/completions', '/api/browse-paths', '/api/attention/bind'}
            read_only = read_only or route == '/api/notifications' and data.get('action') in {'view', 'read', 'open'}
            with app.operation(upgrade_change=not read_only):
                return self.dispatch_post(route, data)
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            payload = {"error": str(exc)}
            if getattr(exc, 'code', None):
                payload['code'] = exc.code
            if getattr(exc, 'next_action', None):
                payload['nextAction'] = exc.next_action
            if isinstance(exc, ControlRestoreRequired) and exc.connection is not None:
                payload['connection'] = exc.connection
            return self.reply(payload, 409 if isinstance(exc, AppClosing) else 400)

    def upload_attachment(self):
        self.close_connection = True
        if (not self.valid_request() or self.headers.get('Content-Type', '').split(';')[0] != 'application/octet-stream'
                or self.headers.get('Transfer-Encoding')):
            return self.reply({'error': '허용되지 않은 파일 전송입니다.'}, 403)
        try:
            size = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= size <= MAX_UPLOAD:
                return self.reply({'error': '끌어서 첨부하는 파일은 50 MB까지 지원합니다. 큰 파일은 경로로 추가해 주세요.'}, 413)
            self.connection.settimeout(30)
            query = parse_qs(urlsplit(self.path).query)
            sid = query.get('id', [''])[0]
            name = unquote(self.headers.get('X-File-Name', ''), errors='strict')
            app = self.server.app
            with app.operation(upgrade_change=True):
                with app.lock:
                    app.get(sid)
                return self.reply(app.attachment_store.save(sid, name, self.rfile, size))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return self.reply({'error': str(exc)}, 409 if isinstance(exc, AppClosing) else 400)

    def dispatch_post(self, route, data):
        app = self.server.app
        sid = data.get("id")
        if route == '/api/app-update':
            action = data.get('action')
            if action == 'check' and set(data) == {'action'}:
                return self.reply(app.app_updates.check(manual=True))
            if action == 'startup' and set(data) == {'action'}:
                return self.reply(app.app_updates.check(startup=True))
            if action == 'configure' and set(data) == {'action', 'autoCheck'}:
                return self.reply(app.app_updates.configure(data['autoCheck']))
            if action == 'install' and set(data) == {'action', 'version'}:
                return self.reply(app.app_updates.install(data['version']))
            raise ValueError('업데이트 요청을 확인해 주세요.')
        if route == '/api/ui-health':
            return self.reply({'ok': True, 'recorded': app.ui_health.frontend(data)})
        if route == '/api/browse-paths':
            from .path_browser import browse_paths, validate_folder_selection
            if data.get('action') == 'select':
                if data.get('kind') not in {'files', 'folder'}:
                    raise ValueError('선택할 파일 또는 폴더 종류를 확인해 주세요.')
                paths = (app.validate_attachments(data.get('paths')) if data['kind'] == 'files'
                         else validate_folder_selection(data.get('paths')))
                return self.reply({'paths': paths})
            if data.get('action') is not None:
                raise ValueError('파일 선택 동작을 확인해 주세요.')
            return self.reply(browse_paths(data))
        if route == '/api/session/branch':
            return self.reply(app.fork_session(sid))
        if route == "/api/create":
            return self.reply(app.create(data.get("workspace", ""), data.get("trusted"), managed=data.get('managed', False), title=data.get('title'), managed_root=data.get('managedRoot')))
        if route == '/api/session/update':
            return self.reply(app.update_session(sid, data))
        if route == '/api/session/hide':
            return self.reply(app.hide_session(sid, data.get('confirmed')))
        if route == '/api/session/reorder':
            return self.reply(app.reorder_session(sid, data.get('targetId'), data.get('position')))
        if route == '/api/reconnect':
            return self.reply(app.reconnect(sid))
        if route == '/api/connect':
            return self.reply(app.connect(sid))
        if route == '/api/control-restore':
            return self.reply(app.accept_current_control(sid, data.get('control'), data.get('action')))
        if route == '/api/window/hide':
            return self.reply(app.hide_window())
        if route == '/api/window/open':
            if not app._open_window_callback:
                raise ValueError('앱 창 연결을 준비하지 못했습니다.')
            return self.reply(app._open_window_callback())
        if route == '/api/dispatch':
            return self.reply(app.dispatch.action(sid, data))
        if route == '/api/claude-sessions/import':
            return self.reply(app.import_session(data.get('sessionId')))
        if route == '/api/notifications':
            action = data.get('action')
            if action == 'configure':
                app.desktop.configure(data.get('preferences', {}))
            elif action == 'read':
                if 'notificationIds' in data:
                    if 'notificationId' in data:
                        raise ValueError('읽음 처리할 알림 목록을 확인해 주세요.')
                    app.desktop.mark_read_many(data['notificationIds'])
                else:
                    app.desktop.mark_read(data.get('notificationId'))
            elif action == 'open':
                app.desktop.open(data.get('notificationId'))
            elif action == 'view':
                with app.lock:
                    if sid:
                        app.get(sid)
                    app._viewed_session = sid if data.get('visible') is True else None
                    app._viewed_until = time.monotonic() + 6
            else:
                raise ValueError('알림 동작을 확인해 주세요.')
            return self.reply({'ok': True, 'desktop': app.desktop.snapshot()})
        if route == '/api/attention/bind':
            # Never accept a client-provided HWND, PID, title, or auth token as
            # native window identity. bind() examines the current OS window.
            return self.reply({'ok': True, 'native': app.notifier.bind()})
        if route == '/api/completions':
            from .completions import complete
            if not sid:
                if data.get('kind') not in {'slash', 'command'}:
                    raise ValueError('파일을 찾을 업무 폴더를 먼저 선택해 주세요.')
                item = {}
            else:
                with app.lock:
                    item = dict(app.get(sid))
                    item['connection'] = dict(item.get('connection') or {})
                    item['attachments'] = list(item.get('attachments', []))
                item['workspace'] = str(workspace_folder(item))
            return self.reply(complete(item, data, safe_suffixes=REFERENCE_FILE_TYPES,
                                       discovery=None if app.demo else app.completion_discovery))
        if route == '/api/model':
            if 'model' not in data:
                raise ValueError('사용할 모델 또는 기본 모델 복원을 선택해 주세요.')
            return self.reply(app.set_model(sid, data['model']))
        if route == '/api/effort':
            if 'effort' not in data:
                raise ValueError('추론 수준 또는 기존 설정 복원을 선택해 주세요.')
            return self.reply(app.set_effort(sid, data['effort']))
        if route == "/api/pick":
            return self.reply(app.pick(data.get("kind"), data.get('initialDirectory')))
        if route == '/api/trust':
            if data.get('trusted') is not True:
                raise ValueError('작업 폴더 확인이 필요합니다.')
            workspace_folder(app.get(sid))
            app.get(sid)['trusted'] = True
            return self.reply({'ok': True})
        if route == "/api/send":
            app.send(sid, data.get("text"), data.get("attachments", []), data.get("trusted"))
        elif route == "/api/respond":
            app.respond(sid, data.get("requestId"), data.get("allow"), data.get("answers"), data.get('permissionChoiceId'))
        elif route == '/api/permission-mode':
            if 'mode' not in data:
                raise ValueError('승인 모드 또는 기존 설정 복원을 선택해 주세요.')
            return self.reply(app.set_permission_mode(sid, data['mode'], bypass_confirmed=data.get('bypassConfirmed') is True))
        elif route == '/api/choice':
            return self.reply(app.answer_choice(sid, data.get('choiceId'), option_id=data.get('optionId'), text=data.get('text')))
        elif route == "/api/stop":
            app.stop(sid)
        elif route == "/api/open":
            path = app.allowed_file(sid, data.get("path", ""))
            from .external_apps import open_document
            result = open_document(path, data.get('action', 'open'))
            if data.get('action', 'open') != 'reveal' and app.get(sid).get('observation'):
                app.get(sid)['observation']['fileOpened'] = True
            return self.reply(result)
        elif route == "/api/native":
            item = app.get(sid)
            app._check_import_context(item)
            if item.get("bridge") and getattr(item['bridge'], 'cleanup_complete', False) is not True:
                raise ValueError("같은 대화의 동시 실행을 막기 위해 먼저 연결을 중지해 주세요.")
            if not item.get("trusted") or not app.command or os.name != "nt" or app.demo:
                raise ValueError("원본 CLI는 폴더 동의 후 Windows 실사용 모드에서 열 수 있습니다.")
            args = app.command[:]
            resume_options = app._resume_options(item)
            if resume_options.get('resume'):
                args.append('--resume=' + str(uuid.UUID(resume_options['resume'])))
            if resume_options.get('fork_session'):
                args.extend(['--fork-session', '--session-id', resume_options['new_session_id']])
            subprocess.Popen(args, cwd=workspace_folder(item), creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            return self.reply({"error": "없는 요청입니다."}, 404)
        return self.reply({"ok": True})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CompanyAgent/local-ui")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Synthetic UI rehearsal; does not start Claude or read source documents")
    args = parser.parse_args()
    from .startup import verify_process
    verify_process()
    if args.demo:
        args.state = args.state / "demo"
    app = LocalApp(args.state, demo=args.demo)
    app._upgrade_headless = args.no_browser
    server = Server(app, args.port)
    url = server.origin + "/#token=" + app.token
    runtime = args.state / "runtime.json"
    from .native_window import DesktopHost, DesktopError
    if args.demo and not args.no_browser:
        app.notifier = AttentionNotifier(enabled=True)
    serving = threading.Event()
    def quit_from_tray():
        if not serving.wait(35) or not app.close():
            return False
        server.shutdown()
        return True
    window = DesktopHost(app.notifier, url, args.state, on_close=quit_from_tray,
                         on_event=app.ui_health.native)
    def reopen():
        app.app_updates.check(startup=True)
        result = window.open()
        app._desktop_window = window
        return result
    app._open_window_callback = reopen
    if not args.no_browser and not args.demo:
        from .tray import WorkspaceTray
        app.tray = WorkspaceTray(str(args.state.resolve()), reopen, quit_from_tray, ASSETS / 'app-icon.ico')
        app.tray.start()
        window.background = app.tray.available is True
    try:
        if not args.no_browser:
            reopen()
        # Publish readiness only after the desktop host passed initialization.
        runtime.write_text(json.dumps({"url": url, "pid": os.getpid(), "port": server.server_port}), encoding="utf-8")
        from .update_install import confirm_running_update
        try:
            confirm_running_update(app.state, WORKSPACE_VERSION)
        except (ValueError, OSError):
            app.update_warning = '업데이트 실행 위치를 저장하지 못했어요. 다음 실행 때 최신 버전 파일을 사용해 주세요.'
        app.dispatch.start()
        app.app_updates.start()
        serving.set()
        server.serve_forever(poll_interval=.3)
    except DesktopError as exc:
        raise SystemExit(exc.code)
    except KeyboardInterrupt:
        pass
    finally:
        closed = app.close()
        window.close()
        if app.tray is not None:
            app.tray.stop()
        server.server_close()
        try:
            if closed and json.loads(runtime.read_text(encoding="utf-8")).get("pid") == os.getpid():
                runtime.unlink()
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    main()
