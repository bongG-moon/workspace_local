"""Loopback-only UI server. All operational endpoints require a per-run capability."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
import uuid
import webbrowser

from .bridge import ClaudeSession, HIDDEN, probe_cli, resolve_cli, runtime_context
from .companion import Companion, begin_turn, course, observe
from .history import HistoryStore, MAX_ARTIFACTS, MAX_SESSIONS, safe
from .artifacts import changes, linked, snapshot
from .capabilities import catalog

ASSETS = Path(__file__).parent / "web"
SAFE_FILES = {".md", ".txt", ".csv", ".tsv", ".html", ".htm", ".pdf", ".pptx", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".webp"}
MAX_BODY = 256 * 1024
MAX_PREVIEW = 1024 * 1024
WORKSPACE_VERSION = "0.11.4"
MANUAL_FILENAME = "Company-Agent-사용자-안내서.html"
MANUAL_CSP = (
    "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; font-src data:; "
    "img-src 'none'; connect-src 'none'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'none'"
)
# Closed URL aliases only: legacy documents need not exist on disk.
MANUAL_ALIASES = {
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
    def __init__(self, state: Path, command=None, info=None, demo=False):
        self.state = state
        self.state.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self._lifecycle = threading.Condition()
        self._active_operations = 0
        self._shutdown_state = 'running'
        self._close_lock = threading.Lock()
        self.sessions = {}
        self.command, self.info, self.demo = command, info or {}, demo
        self._injected_command = command is not None
        self.error = None
        self.managed_workspace_root = self.state / 'workspaces'
        self.reconnect_lock = threading.Lock()
        self.dialog_lock = threading.Lock()
        self.companion = Companion(state, demo=demo)
        if command is None and not demo:
            try:
                self.command = resolve_cli()
                self.info = probe_cli(self.command)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                self.error = str(exc)
        self._load()

    def _load(self):
        self.history = HistoryStore(self.state)
        for item in self.history.load():
            item.update(bridge=None, requests={}, events=[], seq=0, trusted=False, state='idle')
            self.sessions[item['id']] = item

    def save(self, sid=None):
        with self.lock:
            self.history.save(self.sessions.values(), sid)

    def get(self, sid):
        with self.lock:
            if sid not in self.sessions:
                raise ValueError("대화를 찾을 수 없습니다.")
            return self.sessions[sid]

    def public(self, item):
        result = {key: item.get(key) for key in ("id", "title", "workspace", "created", "updated", "pinned", "messages", "state", "trusted", "sessionId", "seq", "lastRunId", "modelOverride") } | {
            "artifacts": list(item.get("artifacts", [])),
            "requests": list(item.get("requests", {}).values())}
        connection = dict(item['connection']) if isinstance(item.get('connection'), dict) else None
        bridge = item.get('bridge')
        live = bool(bridge and not bridge.closed)
        if connection is not None:
            capabilities = dict(connection.get('capabilities') or {})
            if live and isinstance(getattr(bridge, 'capabilities', None), dict):
                capabilities.update(bridge.capabilities)
            if not live:
                capabilities['setModel'] = False
                connection['modelOverride'] = None
            connection.update(capabilities=capabilities, connected=live)
        if not live:
            result['modelOverride'] = None
        result['connection'] = connection
        return result

    def bootstrap(self):
        with self.lock:
            sessions = sorted(self.sessions.values(), key=lambda item: (item.get('pinned') is True,
                              item.get('updated', item['created'])), reverse=True)
            return {"application": "company-workspace", "version": self.info.get("version"), "error": self.error, "demo": self.demo,
                    "workspaceVersion": WORKSPACE_VERSION, "appRoot": str(Path(__file__).resolve().parents[1]), "historyWarning": self.history.warning,
                    "runtime": runtime_context(self.command) if self.command and not self.demo else None,
                    "sessions": [{key: item.get(key) for key in ("id", "title", "workspace", "created", "updated", "pinned", "state")} |
                                 {"artifactCount": len(item.get('artifacts', []))} for item in sessions],
                    "managedWorkspaceRoot": str(self.managed_workspace_root.absolute()),
                    "defaultWorkspace": str(Path.home() / "Desktop" if (Path.home() / "Desktop").is_dir() else Path.home()),
                    **self.shutdown_status()}

    def shutdown_status(self):
        with self._lifecycle:
            return {'closing': self._shutdown_state != 'running',
                    'closed': self._shutdown_state == 'closed',
                    'shutdownState': self._shutdown_state}

    @contextmanager
    def operation(self):
        """Admit mutations before shutdown; drain accepted requests before close.

        The lifecycle condition is separate from the session lock so bootstrap
        remains available while a CLI connection is being stopped.
        """
        with self._lifecycle:
            if self._shutdown_state != 'running':
                raise AppClosing('앱을 종료하고 있습니다. 종료가 끝난 뒤 실행 아이콘으로 다시 열어 주세요.')
            self._active_operations += 1
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

    def create(self, workspace, trusted, *, managed=False, title=None):
        if trusted is not True:
            raise ValueError("이 폴더의 Claude 설정·후크·MCP 실행에 동의해 주세요.")
        if type(managed) is not bool:
            raise ValueError('새 업무 폴더 사용 여부를 확인해 주세요.')
        title = self.clean_title(title) if title is not None else '새 업무'
        with self.lock:
            if len(self.sessions) >= MAX_SESSIONS:
                raise ValueError('업무는 최대 500개까지 저장할 수 있습니다. 기존 업무를 이어서 사용해 주세요.')
            sid = str(uuid.uuid4())
            if managed:
                # The displayed title is never interpreted as a filesystem path.
                base = safe(self.managed_workspace_root.absolute())
                base.mkdir(parents=True, exist_ok=True)
                root = safe(base / (time.strftime('%Y-%m-%d') + '-' + sid[:8]))
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
            if title is not None:
                item['title'] = title
            if 'pinned' in data:
                item['pinned'] = data['pinned']
            item['updated'] = time.time()
            self.save(sid)
            return self.public(item)

    def _finish_observation(self, item):
        before = item.pop('_artifactSnapshot', None)
        if before is None:
            return
        root = Path(item['workspace'])
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
            if item['state'] in {'starting', 'running', 'question', 'approval'} or item.get('_modelUpdating'):
                raise ValueError('현재 업무를 마치거나 중지한 뒤 모델을 변경해 주세요.')
            bridge = item.get('bridge')
            if self.demo or not bridge or bridge.closed or not getattr(bridge, 'ready', threading.Event()).is_set():
                raise ValueError('업무를 한 번 실행해 연결한 뒤, 다음 요청부터 사용할 모델을 선택해 주세요.')
            item['_modelUpdating'] = True
        try:
            # Bridge control responses may emit events: never hold the app lock while waiting.
            result = bridge.set_model(model.strip() if isinstance(model, str) else None)
            with self.lock:
                item['modelOverride'] = result.get('modelOverride')
                return {'ok': True, 'modelOverride': item['modelOverride'], 'session': self.public(item)}
        finally:
            with self.lock:
                item['_modelUpdating'] = False

    def emit(self, sid, kind, data):
        with self.lock:
            item = self.get(sid)
            if kind == "assistant":
                item["messages"].append({"role": "assistant", "text": str(data["text"])[:100000]})
                item["messages"] = item["messages"][-150:]
                # Keep the UI mirror bounded; the CLI owns the full transcript.
                while len(item["messages"]) > 1 and sum(len(row.get("text", "")) for row in item["messages"]) > 500000:
                    item["messages"].pop(0)
            elif kind == "request":
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
                    item['modelOverride'] = None
                    if isinstance(item.get('connection'), dict):
                        item['connection'] = self.public(item)['connection']
                        data['connection'] = item['connection']
            elif kind == "connected":
                item["connection"] = data
                item["sessionId"] = data.get("sessionId")
                item['modelOverride'] = data.get('modelOverride')
            elif kind == "model_changed":
                item.setdefault('connection', {}).update(data)
                item['modelOverride'] = data.get('modelOverride')
            elif kind == "result":
                item["sessionId"] = data.get("sessionId")
                item["state"] = "done"
            elif kind == "error":
                item["state"] = "error"
                item["requests"].clear()
                if "resumeSessionId" in data:
                    item["sessionId"] = data["resumeSessionId"]
            terminal = kind in {'result', 'error'} or kind == 'status' and data.get('state') == 'stopped'
            if terminal:
                self._finish_observation(item)
                data['artifacts'] = list(item.get('artifacts', []))
                data['lastRunId'] = item.get('lastRunId')
            if kind in {'assistant', 'connected', 'result', 'error', 'model_changed'} or terminal:
                item['updated'] = time.time()
            observe(item, kind, data)
            item["seq"] += 1
            item["events"].append({"seq": item["seq"], "type": kind, "data": data})
            item["events"] = item["events"][-300:]
            if kind in {"assistant", "connected", "result", "error", "model_changed"} or terminal:
                self.save(sid)

    def send(self, sid, text, attachments, trusted=False):
        if not isinstance(text, str) or not text.strip() or len(text) > 32000:
            raise ValueError("요청은 1~32,000자로 입력해 주세요.")
        if not isinstance(attachments, list) or len(attachments) > 12:
            raise ValueError("파일은 한 번에 12개까지 선택할 수 있습니다.")
        with self.lock:
            item = self.get(sid)
            if not item.get("trusted") and trusted is not True:
                raise ValueError("다시 시작하기 전에 작업 폴더의 설정 실행에 동의해 주세요.")
            workspace_folder(item)
            if item["state"] in {"starting", "running", "question", "approval"} or item.get('_modelUpdating'):
                raise ValueError("현재 진행 중인 작업을 먼저 마치거나 중지해 주세요.")
            paths = []
            for value in attachments:
                path = Path(value).resolve(strict=True)
                if not path.is_file() or path.suffix.lower() not in SAFE_FILES:
                    raise ValueError("문서 또는 이미지 파일을 선택해 주세요.")
                paths.append(str(path))
            if self.error:
                raise ValueError(self.error)
            if not self.demo:
                bridge = item.get("bridge")
                if bridge is None or bridge.closed:
                    active = sum(1 for row in self.sessions.values() if row.get("bridge") and not row["bridge"].closed)
                    if active >= 3:
                        raise ValueError("연결된 대화가 3개입니다. 다른 대화의 연결을 중지한 뒤 다시 시도해 주세요.")
                    bridge = ClaudeSession(self.command, self.info, Path(item["workspace"]),
                                           lambda kind, data: self.emit(sid, kind, data), item.get("sessionId"))
                    item["bridge"] = bridge
                    item['modelOverride'] = None
                    item.pop('connection', None)  # no stale init evidence during reconnect
            item["trusted"] = True
            item["attachments"] = list(dict.fromkeys(item.get("attachments", []) + paths))
            if item["title"] == "새 업무":
                item["title"] = text.strip().splitlines()[0][:35]
            item["messages"].append({"role": "user", "text": text.strip(), "files": paths})
            item["state"] = "starting"
            item['updated'] = time.time()
            item['lastRunId'] = uuid.uuid4().hex
            item['_artifactSnapshot'] = snapshot(Path(item['workspace']))
            item['artifactObservation'] = item['_artifactSnapshot'].public()
            begin_turn(item)
            self.save(sid)
            # No copied files, skill hardcode, auxiliary inference or rewritten user intent.
            prompt = text.strip()
            if paths:
                prompt += "\n\n사용자가 선택한 원본 파일 경로(JSON):\n" + json.dumps(paths, ensure_ascii=False)
            if self.demo:
                from .demo import run
                threading.Thread(target=run, args=(self, sid, text), daemon=True).start()
            else:
                try:
                    bridge.send(prompt)
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    self.emit(sid, 'error', {'message': str(exc), 'code': 'send_failed'})
                    raise

    def respond(self, sid, rid, allow, answers):
        if type(allow) is not bool:
            raise ValueError("승인 또는 거절을 선택해 주세요.")
        if self.demo:
            from .demo import respond
            respond(self, sid, rid, allow, answers)
        else:
            bridge = self.get(sid).get("bridge")
            if not bridge:
                raise ValueError("연결이 종료된 질문입니다.")
            bridge.respond(rid, allow, answers)

    def stop(self, sid):
        bridge = self.get(sid).get("bridge")
        if bridge:
            bridge.interrupt()
        else:
            self.emit(sid, "status", {"state": "stopped", "label": "중지했어요"})

    def allowed_file(self, sid, value):
        item = self.get(sid)
        path = Path(value).resolve(strict=True)
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

    def pick(self, kind):
        if os.name != "nt":
            raise ValueError("이 버전의 파일 선택 창은 Windows에서 지원합니다. 경로를 입력해 주세요.")
        if kind not in {"folder", "files"}:
            raise ValueError("올바른 선택 종류가 아닙니다.")
        if not self.dialog_lock.acquire(blocking=False):
            raise ValueError("이미 열린 파일 선택 창을 먼저 닫아 주세요.")
        try:
            owner = workspace_window_handle()
            result = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-STA", "-File",
                                     str(Path(__file__).parent / "Pick-Path.ps1"), "-Kind", kind,
                                     "-OwnerHandle", str(owner)],
                                    capture_output=True, encoding="utf-8", timeout=300, creationflags=HIDDEN)
            if result.returncode:
                raise ValueError("파일 선택 창을 열지 못했습니다. 경로를 직접 입력해 주세요.")
            return json.loads(result.stdout.lstrip("\ufeff") or "[]")
        finally:
            self.dialog_lock.release()

    def close(self):
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
            if route.path == "/api/capabilities":
                with app.lock:
                    selected = app.public(app.get(sid)) if sid else None
                return self.reply(catalog(selected, client=app.companion.client,
                                          demo=app.demo, validate_workspace=workspace_folder))
            if route.path == "/api/course":
                return self.reply(course())
            if route.path == "/api/companion":
                item = app.get(sid)
                if not item.get('trusted'):
                    raise ValueError('작업 폴더를 다시 확인한 뒤 관리 화면을 열어 주세요.')
                workspace_folder(item)
                return self.reply(app.companion.snapshot(item, query.get('view', ['checks'])[0]))
            if route.path == "/api/files":
                return self.reply({"files": app.files(sid)})
            if route.path == "/api/results":
                return self.reply(app.results(sid))
            if route.path == "/api/preview":
                path = app.allowed_file(sid, query.get("path", [""])[0])
                if path.stat().st_size > MAX_PREVIEW:
                    return self.reply({"kind": "external", "name": path.name, "message": "큰 파일은 원래 앱에서 열어 주세요."})
                if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    mime = {".png": "image/png", ".webp": "image/webp"}.get(path.suffix.lower(), "image/jpeg")
                    raw = path.read_bytes()
                    if app.get(sid).get('observation'):
                        app.get(sid)['observation']['previewed'].add(str(path))
                    return self.reply({"kind": "image", "name": path.name, "data": "data:" + mime + ";base64," + base64.b64encode(raw).decode()})
                if path.suffix.lower() in {".md", ".txt", ".csv", ".tsv", ".html", ".htm"}:
                    text = path.read_text(encoding="utf-8-sig")
                    if app.get(sid).get('observation'):
                        app.get(sid)['observation']['previewed'].add(str(path))
                    if path.suffix.lower() in {'.html', '.htm'}:
                        from .html_preview import render
                        preview = render(text)
                        if preview:
                            return self.reply({'kind':'html', 'name':path.name, 'html':preview,
                                               'message':'정적 미리보기입니다. 모든 구역을 표시하며 스크립트·외부 연결은 실행하지 않습니다. 선택은 채팅으로 알려 주세요.'})
                    return self.reply({"kind": "text", "name": path.name, "text": text})
                return self.reply({"kind": "external", "name": path.name, "message": "Office·PDF 원본은 원래 앱에서 열어 확인해 주세요."})
            manual_path = unquote(route.path)
            if manual_path in {'/manual/guide', '/manual/' + MANUAL_FILENAME}:
                return self.reply((ASSETS.parent.parent / 'docs' / MANUAL_FILENAME).read_bytes(),
                                  content_type='text/html; charset=utf-8', csp=MANUAL_CSP)
            if manual_path in MANUAL_ALIASES:
                return self.reply({}, 302, location='/manual/guide' + MANUAL_ALIASES[manual_path])
            assets = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/companion.js": ("companion.js", "text/javascript; charset=utf-8"),
                      "/capabilities.js": ("capabilities.js", "text/javascript; charset=utf-8"),
                      "/app.css": ("app.css", "text/css; charset=utf-8"),
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
            if route == "/api/quit":
                if not app.close():
                    return self.reply({'ok': False, **app.shutdown_status(),
                                       'error': 'CLI 연결의 종료를 확인하지 못했습니다. 진행 중인 작업을 확인한 뒤 다시 시도해 주세요.'}, 503)
                try:
                    return self.reply({'ok': True, **app.shutdown_status()})
                finally:
                    # Deliver completion only after cleanup, then stop accepting HTTP.
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
            with app.operation():
                return self.dispatch_post(route, data)
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            payload = {"error": str(exc)}
            if getattr(exc, 'code', None):
                payload['code'] = exc.code
            if getattr(exc, 'next_action', None):
                payload['nextAction'] = exc.next_action
            return self.reply(payload, 409 if isinstance(exc, AppClosing) else 400)

    def dispatch_post(self, route, data):
        app = self.server.app
        sid = data.get("id")
        if route == "/api/create":
            return self.reply(app.create(data.get("workspace", ""), data.get("trusted"), managed=data.get('managed', False), title=data.get('title')))
        if route == '/api/session/update':
            return self.reply(app.update_session(sid, data))
        if route == '/api/reconnect':
            return self.reply(app.reconnect(sid))
        if route == '/api/model':
            if 'model' not in data:
                raise ValueError('사용할 모델 또는 기본 모델 복원을 선택해 주세요.')
            return self.reply(app.set_model(sid, data['model']))
        if route == "/api/pick":
            return self.reply({"paths": app.pick(data.get("kind"))})
        if route == '/api/trust':
            if data.get('trusted') is not True:
                raise ValueError('작업 폴더 확인이 필요합니다.')
            workspace_folder(app.get(sid))
            app.get(sid)['trusted'] = True
            return self.reply({'ok': True})
        if route == '/api/companion':
            item = app.get(sid)
            if not item.get('trusted'):
                raise ValueError('작업 폴더를 확인한 뒤 진행해 주세요.')
            workspace_folder(item)
            if item['state'] in {'starting', 'running', 'question', 'approval'} and data.get('action') in {'apply', 'learning', 'rollback', 'share'}:
                raise ValueError('진행 중인 업무를 마치거나 중지한 뒤 기억·지침을 변경해 주세요.')
            return self.reply(app.companion.action(item, data))
        if route == "/api/send":
            app.send(sid, data.get("text"), data.get("attachments", []), data.get("trusted"))
        elif route == "/api/respond":
            app.respond(sid, data.get("requestId"), data.get("allow"), data.get("answers"))
        elif route == "/api/stop":
            app.stop(sid)
        elif route == "/api/open":
            path = app.allowed_file(sid, data.get("path", ""))
            if os.name != "nt":
                raise ValueError("원래 앱 열기는 Windows에서 지원합니다.")
            # No raw attachment execution; supported HTML uses the isolated static preview.
            if path.suffix.lower() in {".html", ".htm"}:
                raise ValueError("HTML은 앱 안의 정적 미리보기 또는 소스 보기로 확인해 주세요. 원본 스크립트는 실행하지 않습니다.")
            os.startfile(str(path))
            if app.get(sid).get('observation'):
                app.get(sid)['observation']['fileOpened'] = True
        elif route == "/api/native":
            item = app.get(sid)
            if item.get("bridge") and not item["bridge"].closed:
                raise ValueError("같은 대화의 동시 실행을 막기 위해 먼저 연결을 중지해 주세요.")
            if not item.get("trusted") or not app.command or os.name != "nt" or app.demo:
                raise ValueError("원본 CLI는 폴더 동의 후 Windows 실사용 모드에서 열 수 있습니다.")
            args = app.command[:]
            if item.get("sessionId"):
                args.append("--resume=" + str(uuid.UUID(item["sessionId"])))
            subprocess.Popen(args, cwd=workspace_folder(item), creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            return self.reply({"error": "없는 요청입니다."}, 404)
        return self.reply({"ok": True})


def open_window(url):
    if os.name == "nt":
        for root in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
            if root:
                edge = Path(root) / "Microsoft/Edge/Application/msedge.exe"
                if edge.is_file():
                    subprocess.Popen([str(edge), "--app=" + url, "--new-window"], creationflags=HIDDEN)
                    return
    webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CompanyAgent/local-ui")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Synthetic UI rehearsal; does not start Claude or read source documents")
    args = parser.parse_args()
    if args.demo:
        args.state = args.state / "demo"
    app = LocalApp(args.state, demo=args.demo)
    server = Server(app, args.port)
    url = server.origin + "/#token=" + app.token
    runtime = args.state / "runtime.json"
    runtime.write_text(json.dumps({"url": url, "pid": os.getpid(), "port": server.server_port}), encoding="utf-8")
    if not args.no_browser:
        open_window(url)
    try:
        server.serve_forever(poll_interval=.3)
    except KeyboardInterrupt:
        pass
    finally:
        closed = app.close()
        server.server_close()
        try:
            if closed and json.loads(runtime.read_text(encoding="utf-8")).get("pid") == os.getpid():
                runtime.unlink()
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    main()
