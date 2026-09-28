"""Bidirectional Claude Code transport. No LLM routing, config rewriting or shell eval.

The small wire adapter follows the public Anthropic Agent SDK control protocol.
Installed filesystem hooks, skills and MCP are handled by the CLI, not emulated.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque

HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_FRAME = 8 * 1024 * 1024
CONTROL_TIMEOUT = 10


class BridgeError(ValueError):
    """An actionable, non-secret failure that the GUI can present directly."""
    def __init__(self, code: str, message: str, next_action: str):
        super().__init__(message)
        self.code, self.next_action = code, next_action


def resolve_cli(value: str | None = None) -> list[str]:
    """Resolve only the active user's PATH/explicit selection; never scan profiles."""
    explicit = value or os.environ.get("COMPANY_AGENT_CLAUDE")
    raw = explicit or os.environ.get("COMPANY_WORKSPACE_CLAUDE_ENTRY") or shutil.which("claude")
    if not raw:
        raise ValueError("Claude Code를 찾지 못했습니다. 기존 CLI 설치를 먼저 확인해 주세요.")
    from_profile = not explicit and os.environ.get("COMPANY_WORKSPACE_CLAUDE_PROFILE") == "1"
    if from_profile:
        if raw != "claude":
            raise ValueError("터미널의 Claude 호출 정보를 확인하지 못했습니다.")
        return terminal_command(raw, load_profiles=True)
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise ValueError("선택한 Claude Code 실행 파일이 없습니다.")
    if path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        # Honor the selected shim itself: it may set corporate auth/configuration.
        # Skipping it for a sibling npm binary changes what terminal `claude` does.
        return terminal_command(str(path))
    return [str(path)]


def terminal_command(entry: str, load_profiles=False) -> list[str]:
    shell = os.environ.get("COMPANY_WORKSPACE_SHELL") or shutil.which("powershell.exe")
    if os.name != "nt" or not shell or not Path(shell).is_file():
        raise ValueError("기존 터미널의 Claude 실행 환경을 연결하지 못했습니다. 별도 로그인은 필요하지 않습니다.")
    args = [shell, "-NoLogo", "-NoProfile", "-File",
            str(Path(__file__).with_name("Invoke-TerminalClaude.ps1")), "-Entry", entry]
    if load_profiles:
        args.append("-LoadProfiles")
    return args


def runtime_context(command: list[str]) -> dict:
    """Only non-secret context. Credentials remain owned/read by Claude Code."""
    entry = command[command.index("-Entry") + 1] if "-Entry" in command else command[0]
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()
    return {"entry": entry, "configRoot": str(root), "authentication": "shared-with-cli"}


def probe_cli(command: list[str]) -> dict:
    info = {}
    for flag, key in [("--version", "version"), ("--help", "help")]:
        result = subprocess.run(command + [flag], capture_output=True, encoding="utf-8",
                                errors="replace", timeout=12, creationflags=HIDDEN)
        if result.returncode:
            raise ValueError("기존 터미널의 Claude 실행 환경을 연결하지 못했습니다. 앱 전용 로그인·재설치 대신 터미널의 실행 경로와 시작 설정을 확인해 주세요.")
        info[key] = result.stdout.strip()
    required = ["--input-format", "--output-format", "--permission-prompt-tool"]
    if any(flag not in info["help"] for flag in required):
        raise ValueError("이 Claude Code 버전에는 필요한 대화 연결 기능이 없습니다. CLI 버전을 확인해 주세요.")
    return info


def cli_arguments(command: list[str], info: dict, resume: str | None = None) -> list[str]:
    args = command + ["--print", "--verbose", "--input-format", "stream-json",
                      "--output-format", "stream-json", "--permission-prompt-tool", "stdio"]
    # Only the I/O changes. Let Claude choose its existing settings, model,
    # authentication and permission mode; the UI must not create its own defaults.
    if "--include-partial-messages" in info.get("help", ""):
        args.append("--include-partial-messages")
    if resume:
        args.append("--resume=" + str(uuid.UUID(resume)))
    return args


class ClaudeSession:
    def __init__(self, command: list[str], info: dict, cwd: Path, emit, resume=None):
        self.command, self.info, self.cwd, self.emit = command, info, cwd, emit
        self.session_id = resume
        self.resume_id = resume
        self.process = None
        self.lock = threading.RLock()
        self.ready = threading.Event()
        self.pending = {}
        self.tasks = set()
        self.closed = False
        self._close_done = threading.Event()
        self._close_owner = None
        self._close_error = None
        self._readers = []
        self.stopping = False
        self.busy = False
        self.initialization_error = None
        self.stderr = deque(maxlen=30)
        self.initialize_id = "initialize-" + uuid.uuid4().hex
        self.last_result = None
        self.seen_text = set()
        self.model = ""
        self.original_model = None
        self.model_override = None
        self.available_models = []
        self.slash_commands = []
        self._commands_reported = False
        self._initialized = False
        self._model_control_supported = True
        self._control_active = False
        self._control_waiters = {}
        self._stream_message_id = None
        self._stream_blocks = {}
        self._stream_index = None
        self._finalized_blocks = set()

    @property
    def capabilities(self) -> dict:
        return {"partialMessages": "--include-partial-messages" in self.info.get("help", ""),
                "setModel": bool(self._initialized and self.original_model and
                                 self._model_control_supported and not self.closed)}

    def model_state(self) -> dict:
        return {"model": self.model, "modelOverride": self.model_override,
                "availableModels": self.available_models, "capabilities": self.capabilities}

    def set_model(self, model: str | None) -> dict:
        """Change only this live CLI connection, after its control acknowledgement.

        The SDK wire request is {subtype: set_model, model: ...}. Resetting sends
        the model observed at init, preserving the selected shim/provider default.
        """
        if model is not None:
            if (not isinstance(model, str) or not model.strip() or len(model) > 200 or
                    any(ord(char) < 32 for char in model)):
                raise BridgeError("model_invalid", "모델 이름을 정확히 입력해 주세요.", "모델 이름 확인")
            model = model.strip()
        with self.lock:
            if self.busy or self.pending or self._control_active:
                raise BridgeError("session_busy", "현재 작업과 확인 요청이 끝난 뒤 모델을 변경해 주세요.", "작업 완료 기다리기")
            if not self.capabilities["setModel"] or not self.process or self.process.poll() is not None:
                raise BridgeError("model_unavailable", "업무 연결이 준비된 뒤 모델을 변경할 수 있습니다.", "연결 상태 확인")
            selected = model if model is not None else self.original_model
            rid = "model-" + uuid.uuid4().hex
            waiter = {"event": threading.Event(), "response": None}
            self._control_waiters[rid] = waiter
            self._control_active = True
        try:
            self._write({"type": "control_request", "request_id": rid,
                         "request": {"subtype": "set_model", "model": selected}})
            if not waiter["event"].wait(CONTROL_TIMEOUT):
                # The change may have reached the CLI. Retire this child so the
                # next request cannot silently use an unconfirmed model.
                self.close()
                raise BridgeError("model_timeout", "모델 변경 응답을 확인하지 못해 업무 연결을 종료했습니다. 다음 요청에서 기존 설정으로 다시 연결합니다.", "다음 요청으로 다시 연결")
            response = waiter["response"]
            if self.closed or response is None:
                raise BridgeError("connection_closed", "모델을 변경하기 전에 업무 연결이 종료되었습니다.", "다음 요청으로 다시 연결")
            if response.get("subtype") != "success":
                error = str(response.get("error", "")).casefold()
                if any(word in error for word in ("unsupported", "unknown request", "unknown subtype")):
                    self._model_control_supported = False
                raise BridgeError("model_rejected", "현재 연결에서 모델 변경이 거절되었습니다. 회사에서 사용할 수 있는 모델 이름과 연결 상태를 확인해 주세요.", "모델 이름과 연결 확인")
            with self.lock:
                self.model, self.model_override = selected, model
                state = self.model_state()
            self.emit("model_changed", state)
            return state
        finally:
            with self.lock:
                self._control_waiters.pop(rid, None)
                self._control_active = False

    def _auth_error(self, text: str) -> bool:
        text = text.casefold()
        return any(value in text for value in ("failed to authenticate", "oauth session expired",
            "authentication_failed", "not logged in", "invalid authentication credentials"))

    def _authentication_failed(self):
        # The terminal may refresh the shared credential while this child is
        # alive. Drop only our failed child; the next explicit send reloads it.
        # Never replay a business request, copy credentials or initiate a login.
        self.close()
        self.emit("error", {"code": "cli_authentication", "resumeSessionId": self.resume_id,
            "nextAction": "연결 상태 확인 후 다시 보내기",
            "message": "기존 Claude Code의 인증을 확인하지 못했습니다. 이 앱은 터미널과 같은 로그인을 사용하며 별도 계정 설정은 없습니다. "
                       "터미널에서 Claude가 정상 응답하는지 확인한 뒤 다시 보내 주세요. 앱은 다음 요청에서 갱신된 인증을 다시 읽습니다."})

    def start(self):
        with self.lock:
            if self.closed or self.stopping:
                raise ValueError("중지된 연결은 시작하지 않습니다.")
            env = dict(os.environ)
            # Encoding only; auth, provider, model, config-root and policy stay untouched.
            env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            self.process = subprocess.Popen(cli_arguments(self.command, self.info, self.session_id),
                cwd=self.cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=HIDDEN)
            self._readers = [threading.Thread(target=self._read, daemon=True),
                             threading.Thread(target=self._read_stderr, daemon=True)]
            for reader in self._readers:
                reader.start()
            self._write({"type": "control_request", "request_id": self.initialize_id,
                         "request": {"subtype": "initialize"}})

    def _write(self, value):
        with self.lock:
            if self.closed or not self.process or self.process.poll() is not None:
                raise ValueError("대화 연결이 종료되었습니다. 새 대화에서 다시 시작해 주세요.")
            self.process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
            self.process.stdin.flush()

    def send(self, prompt: str):
        with self.lock:
            if self.busy or self._control_active:
                raise ValueError("현재 작업 또는 질문이 끝난 뒤 다음 메시지를 보내 주세요.")
            self.busy = True
            self.last_result = None
            self.seen_text.clear()
            self._stream_blocks.clear()
            self._finalized_blocks.clear()
            self._stream_message_id = self._stream_index = None
        self.emit("status", {"state": "starting", "label": "기존 Claude 설정을 연결하고 있어요"})
        threading.Thread(target=self._send_when_ready, args=(prompt,), daemon=True).start()

    def _send_when_ready(self, prompt):
        try:
            if self.process is None:
                self.start()
            if not self.ready.wait(60):
                raise ValueError("CLI 준비 응답이 60초 동안 없습니다. 로그인·MCP·시작 후크 상태를 확인해 주세요.")
            if self.stopping or self.closed:
                return
            if self.initialization_error:
                raise ValueError(self.initialization_error)
            self._write({"type": "user", "message": {"role": "user", "content": prompt},
                         "session_id": self.session_id or "", "parent_tool_use_id": None})
            self.emit("status", {"state": "running", "label": "요청을 처리하고 있어요"})
        except Exception as exc:
            if not self.stopping and not self.closed:
                self.emit("error", {"message": str(exc)})
            self.close()

    def _read_stderr(self):
        try:
            for line in iter(self.process.stderr.readline, b""):
                # Never persist or expose stderr automatically: it can contain secrets.
                self.stderr.append(line.decode("utf-8", errors="replace")[:2000])
        finally:
            self.process.stderr.close()

    def _read(self):
        try:
            while not self.closed:
                line = self.process.stdout.readline(MAX_FRAME + 1)
                if not line:
                    break
                if len(line) > MAX_FRAME:
                    raise ValueError("CLI 응답 한 건이 화면 처리 한도를 초과했습니다. 읽기 범위를 줄여 주세요.")
                try:
                    data = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise ValueError("CLI가 예상한 UTF-8 대화 형식으로 응답하지 않았습니다.")
                if isinstance(data, dict):
                    self.handle(data)
        except Exception as exc:
            if not self.closed and not self.stopping:
                self.emit("error", {"message": str(exc)})
        finally:
            if not self.closed and not self.stopping:
                self.emit("error", {"message": "CLI 연결이 종료되었습니다. 로그인 또는 실행 환경을 확인해 주세요."})
            self.close()
            self.process.stdout.close()

    def _handle_stream(self, data: dict):
        # A delegated agent's text must never become the coordinator's answer.
        if data.get("parent_tool_use_id"):
            return
        event = data.get("event", {})
        kind = event.get("type")
        if kind == "message_start":
            self._stream_message_id = event.get("message", {}).get("id") or "stream-" + uuid.uuid4().hex
            self._stream_index = None
        elif kind in {"content_block_start", "content_block_delta"}:
            index = event.get("index")
            if not isinstance(index, int) or index < 0 or self._stream_message_id is None:
                return
            self._stream_index = index
            block = event.get("content_block", {}) if kind == "content_block_start" else event.get("delta", {})
            if block.get("type") not in {"text", "text_delta"}:
                return
            text = block.get("text", "")
            if not isinstance(text, str):
                return
            key = (self._stream_message_id, index)
            if key in self._finalized_blocks:
                return
            self._stream_blocks[key] = self._stream_blocks.get(key, "") + text
            if text:
                self.emit("assistant_delta", {"messageId": key[0], "index": index, "text": text})

    def _assistant_text(self, text: str, message_id=None, index=0):
        if not text:
            return
        # Complete messages may contain a single block even when the raw stream
        # block index is greater than zero. Match that block to its streamed text.
        matches = [key for key, value in self._stream_blocks.items()
                   if value == text and (message_id is None or key[0] == message_id)]
        key = next((key for key in matches if key not in self._finalized_blocks), None)
        if key is None and matches:
            return
        if key is None:
            active_key = (message_id, self._stream_index)
            key = (active_key if active_key in self._stream_blocks and active_key not in self._finalized_blocks
                   else (message_id or "message-" + uuid.uuid4().hex, index))
            if text in self.seen_text:
                return
            # Without raw stream events, separate single-block assistant frames
            # share a message ID and each enumerates at zero. Give later blocks
            # a stable free index instead of losing their text.
            while key in self._finalized_blocks:
                key = (key[0], key[1] + 1)
        self._finalized_blocks.add(key)
        self.seen_text.add(text)
        self.emit("assistant", {"messageId": key[0], "index": key[1], "text": text})

    def handle(self, data: dict):
        kind = data.get("type")
        if kind == "control_response":
            response = data.get("response", {})
            if response.get("request_id") == self.initialize_id:
                if response.get("subtype") == "error":
                    self.initialization_error = "CLI 대화 초기화가 거절되었습니다. 설치 버전을 확인해 주세요."
                else:
                    self._initialized = True
                    detail = response.get("response", {})
                    if isinstance(detail, dict):
                        if isinstance(detail.get("commands"), list):
                            self.slash_commands = detail["commands"]
                            self._commands_reported = True
                        if isinstance(detail.get("models"), list):
                            self.available_models = detail["models"]
                self.ready.set()
            else:
                with self.lock:
                    waiter = self._control_waiters.get(response.get("request_id"))
                    if waiter is not None:
                        waiter["response"] = response
                        waiter["event"].set()
        elif kind == "control_request":
            request, rid = data.get("request", {}), data.get("request_id")
            if not isinstance(rid, str) or not rid:
                raise ValueError("CLI 질문 식별자가 올바르지 않습니다.")
            if request.get("subtype") != "can_use_tool":
                self._write({"type": "control_response", "response": {"subtype": "error",
                    "request_id": rid, "error": "This local UI does not support this control request."}})
                self.emit("notice", {"message": "이 요청은 현재 화면에서 지원하지 않습니다. 원본 CLI에서 확인해 주세요."})
                return
            with self.lock:
                if rid in self.pending:
                    return
                self.pending[rid] = request
            self.emit("request", {"id": rid, "tool": request.get("tool_name", ""),
                "input": request.get("input", {}), "description": request.get("description", ""),
                "title": request.get("title", ""), "agent": request.get("agent_id")})
        elif kind == "control_cancel_request":
            with self.lock:
                self.pending.pop(data.get("request_id"), None)
            self.emit("request_closed", {"id": data.get("request_id")})
        elif kind == "system":
            subtype = data.get("subtype")
            if subtype == "init":
                self.session_id = data.get("session_id") or self.session_id
                self.model = data.get("model", "")
                if self.original_model is None and self.model:
                    self.original_model = self.model
                if isinstance(data.get("slash_commands"), list):
                    # system/init is authoritative for which names are listed;
                    # initialize often supplies richer descriptions for them.
                    descriptions = {row.get('name'): row for row in self.slash_commands
                                    if isinstance(row, dict) and isinstance(row.get('name'), str)}
                    self.slash_commands = [descriptions.get(row, row) if isinstance(row, str)
                                           else {**descriptions.get(row.get('name') if isinstance(row.get('name'), str) else '', {}), **row}
                                           if isinstance(row, dict) else row
                                           for row in data["slash_commands"]]
                    self._commands_reported = True
                self.emit("connected", {"sessionId": self.session_id, **self.model_state(),
                    "slashCommands": self.slash_commands,
                    "skills": data.get("skills", []), "plugins": data.get("plugins", []),
                    "mcp": data.get("mcp_servers", []), "tools": data.get("tools", []),
                    "reported": {"commands": self._commands_reported,
                                 **{name: isinstance(data.get(field), list) for name, field in
                                    (("skills", "skills"), ("plugins", "plugins"),
                                     ("mcp", "mcp_servers"), ("tools", "tools"))}}})
            elif subtype == "task_started" and data.get("task_type") in {"local_agent", "local_workflow"}:
                self.tasks.add(data.get("task_id"))
                self.emit("status", {"state": "running", "label": "담당 작업자가 처리하고 있어요"})
            elif subtype == "task_notification" or (subtype == "task_updated" and
                    data.get("patch", {}).get("status") in {"completed", "failed", "stopped"}):
                self.tasks.discard(data.get("task_id"))
                # Do not announce completion yet; the coordinator still needs its next result.
            elif subtype == "status":
                self.emit("status", {"state": "running", "label": "작업을 계속하고 있어요"})
        elif kind == "stream_event":
            self._handle_stream(data)
        elif kind == "assistant":
            if data.get("error") == "authentication_failed":
                self._authentication_failed()
                return
            self.session_id = data.get("session_id") or self.session_id
            parent = data.get("parent_tool_use_id")
            message = data.get("message", {})
            for index, block in enumerate(message.get("content", [])):
                if block.get("type") == "text" and not parent:
                    self._assistant_text(block.get("text", ""), message.get("id"), index)
                elif block.get("type") == "tool_use":
                    self.emit("activity", {"tool": block.get("name"), "id": block.get("id"),
                        "skill": block.get("input", {}).get("skill") if block.get("name") == "Skill" else None})
        elif kind == "result":
            self.last_result = data
            self.session_id = data.get("session_id") or self.session_id
            text = data.get("result", "")
            error_text = "; ".join(map(str, data.get("errors", []))) or text
            if data.get("is_error") and self._auth_error(error_text):
                self._authentication_failed()
                return
            if text and text not in self.seen_text:
                self._assistant_text(text)
            if data.get("is_error"):
                self.busy = False
                self.emit("error", {"code": "task_failed", "nextAction": "자료와 연결 상태를 확인하고 다시 요청",
                    "message": "; ".join(map(str, data.get("errors", []))) or text or "작업을 완료하지 못했습니다."})
            elif not self.tasks:
                self.busy = False
                self.resume_id = self.session_id
                self.emit("result", {"sessionId": self.session_id, "durationMs": data.get("duration_ms"),
                    "usage": data.get("usage", {}), "costUsd": data.get("total_cost_usd")})
            else:
                self.emit("status", {"state": "running", "label": "작업자의 결과를 기다리고 있어요"})

    def respond(self, rid: str, allow: bool, answers=None):
        with self.lock:
            request = self.pending.get(rid)
            if request is None:
                raise ValueError("이미 처리되었거나 만료된 질문입니다.")
            original = request.get("input", {})
            updated = dict(original)
            if request.get("tool_name") == "AskUserQuestion" and allow:
                questions = original.get("questions", [])
                if not isinstance(answers, dict) or any(not isinstance(answers.get(q.get("question")), str)
                    or not answers[q["question"]].strip() for q in questions):
                    raise ValueError("각 질문의 답변을 선택하거나 입력해 주세요.")
                updated["answers"] = {q["question"]: answers[q["question"]][:8000] for q in questions}
            response = {"behavior": "allow", "updatedInput": updated} if allow else {
                "behavior": "deny", "message": "사용자가 이번 요청을 거절했습니다. 다른 권한이나 우회 실행으로 재시도하지 마세요."}
            self._write({"type": "control_response", "response": {"subtype": "success",
                        "request_id": rid, "response": response}})
            del self.pending[rid]
        self.emit("request_closed", {"id": rid})

    def close(self):
        current = threading.current_thread()
        with self.lock:
            if self.closed:
                # Readers may be leaving a callback while another closer waits
                # for the owned process. They must not wait on that closer.
                reentrant = current is self._close_owner or current in self._readers
                owner = False
            else:
                self.closed, self.busy = True, False
                self.pending.clear()
                for waiter in self._control_waiters.values():
                    waiter["event"].set()
                self._close_owner = current
                process = self.process
                owner = True
        if not owner:
            if reentrant:
                return self._close_done.is_set() and self._close_error is None
            return self._close_done.wait(21) and self._close_error is None
        self.ready.set()
        try:
            if process and process.poll() is None:
                # EOF lets an owned terminal wrapper reap its CLI child before
                # forced shutdown. This is bounded, not a business-request retry.
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass
                try:
                    process.wait(timeout=.3)
                    needs_stop = False
                except subprocess.TimeoutExpired:
                    needs_stop = True
            else:
                needs_stop = False
            if needs_stop:
                if os.name == "nt":
                    try:
                        result = subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                                                capture_output=True, creationflags=HIDDEN, timeout=10)
                        tree_stopped = result.returncode == 0
                    except (OSError, subprocess.TimeoutExpired):
                        tree_stopped = False
                    if not tree_stopped:
                        # Use only the handle we created, never search/kill by
                        # name. This fallback cannot prove descendant shutdown.
                        process.kill()
                else:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process:
                # Reader threads own their output streams. Closing a buffered
                # stream from here could wait on their blocking readline lock.
                streams = (process.stdin,) if self._readers else (process.stdin, process.stdout, process.stderr)
                for stream in streams:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass
        except (OSError, subprocess.TimeoutExpired):
            self._close_error = "앱에서 시작한 CLI의 종료를 확인하지 못했습니다. 이미 실행된 작업이나 하위 프로그램의 상태를 확인해 주세요."
            self.emit("error", {"message": self._close_error})
        finally:
            self._close_done.set()
        return self._close_error is None

    def interrupt(self):
        # Stop only this app-owned process. Never kill arbitrary claude/Office processes.
        if self.closed:
            return
        self.stopping = True
        if self.process is None:
            if self.close():
                # start() may have been inside Popen while we observed None.
                # close() waits for that owner; report only its actual result.
                label = ("시작 전에 중지했어요" if self.process is None else
                         "앱의 CLI 연결을 중지했어요 · 이미 만들어진 파일은 유지됩니다")
                self.emit("status", {"state": "stopped", "label": label})
            return
        try:
            self._write({"type": "control_request", "request_id": "stop-" + uuid.uuid4().hex,
                         "request": {"subtype": "interrupt"}})
        except (ValueError, BrokenPipeError, OSError):
            pass  # The owned process may have exited just before the stop click.
        finally:
            threading.Thread(target=self._finish_stop, daemon=True).start()

    def _finish_stop(self):
        time.sleep(1)
        if self.close():
            self.emit("status", {"state": "stopped", "label": "앱의 CLI 연결을 중지했어요 · 이미 만들어진 파일은 유지됩니다"})
