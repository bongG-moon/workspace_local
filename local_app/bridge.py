"""Bidirectional Claude Code transport. No LLM routing, config rewriting or shell eval.

The small wire adapter follows the public Anthropic Agent SDK control protocol.
Installed filesystem hooks, skills and MCP are handled by the CLI, not emulated.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid
from collections import OrderedDict, deque

from .permission_contract import help_permission_modes, help_bypass_opt_in, BYPASS_MODE, mode_options, mode_label, mode_cycle, mode_wire_value, observed_mode, session_choices, request_context

HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_FRAME = 8 * 1024 * 1024
MAX_TURN_EVIDENCE = 10000
CONTROL_TIMEOUT = 10
PREPARE_TIMEOUT = 60
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


def _remember_evidence(mapping, key, value=True):
    mapping[key] = value
    if len(mapping) > MAX_TURN_EVIDENCE:
        del mapping[next(iter(mapping))]


def _text_fingerprint(text):
    return hashlib.sha256(text.encode('utf-8', errors='replace')).digest()


class BridgeError(ValueError):
    """An actionable, non-secret failure that the GUI can present directly."""
    def __init__(self, code: str, message: str, next_action: str):
        super().__init__(message)
        self.code, self.next_action = code, next_action


class ControlRestoreRequired(BridgeError):
    """Remembered controls need input; the business prompt was never submitted."""
    def __init__(self, message=None, *, connection=None):
        super().__init__('control_restore_required',
                         message or '이전 선택을 현재 연결에 적용하지 못했습니다. 모델·추론 수준·승인 모드를 확인한 뒤 다시 보내 주세요.',
                         '현재 연결에서 제공한 설정 선택')
        self.connection = connection


def resolve_cli(value: str | None = None) -> list[str]:
    """Resolve only the active user's PATH/explicit selection; never scan profiles."""
    explicit = value or os.environ.get("COMPANY_AGENT_CLAUDE")
    if not explicit and os.environ.get('COMPANY_WORKSPACE_CLAUDE_UNAVAILABLE') == '1':
        raise ValueError('앱은 열렸지만 기존 터미널의 Claude 실행 명령을 확인하지 못했습니다. '
                         '평소 사용하는 터미널에서 Claude 실행을 확인한 뒤 앱을 완전히 종료하고 다시 열어 주세요. '
                         '앱 전용 로그인은 필요하지 않습니다.')
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
    args = [shell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
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


def cli_arguments(command: list[str], info: dict, resume: str | None = None, *, allow_bypass_permissions=False,
                  fork_session=False, new_session_id=None) -> list[str]:
    if type(allow_bypass_permissions) is not bool:
        raise ValueError('Bypass 선택 준비 여부를 확인해 주세요.')
    if allow_bypass_permissions and not help_bypass_opt_in(info.get('help', '')):
        raise BridgeError('bypass_unavailable', '설치된 CLI에서 Bypass 선택 준비 옵션을 확인하지 못했습니다.', '기존 승인 모드 사용')
    if type(fork_session) is not bool:
        raise ValueError('대화 분기 여부를 확인해 주세요.')
    if fork_session:
        from .conversation_fork import fork_capability
        from .session_import import session_uuid
        if not fork_capability(info)['available']:
            raise BridgeError('fork_unavailable', '설치된 CLI에서 독립 대화 분기 옵션을 확인하지 못했습니다.', 'Claude Code 버전 확인')
        source_id, target_id = session_uuid(resume), session_uuid(new_session_id)
        if source_id == target_id:
            raise ValueError('분기할 대화는 원본과 다른 세션 ID여야 합니다.')
    elif new_session_id is not None:
        raise ValueError('새 세션 ID는 독립 대화 분기에만 지정할 수 있습니다.')
    args = command + ["--print", "--verbose", "--input-format", "stream-json",
                      "--output-format", "stream-json", "--permission-prompt-tool", "stdio"]
    # Only the I/O changes. Let Claude choose its existing settings, model,
    # authentication and permission mode; the UI must not create its own defaults.
    # Only an explicitly confirmed app-session opt-in may enable the candidate;
    # it does not activate bypass and never sets a starting permission mode.
    if allow_bypass_permissions:
        args.append('--allow-dangerously-skip-permissions')
    if "--include-partial-messages" in info.get("help", ""):
        args.append("--include-partial-messages")
    if resume:
        args.append("--resume=" + str(uuid.UUID(resume)))
    if fork_session:
        args.extend(['--fork-session', '--session-id=' + target_id])
    return args


class ClaudeSession:
    def __init__(self, command: list[str], info: dict, cwd: Path, emit, resume=None, *, choice_helper=None,
                 allow_bypass_permissions=False, fork_session=False, new_session_id=None, require_resume_identity=False):
        if type(allow_bypass_permissions) is not bool:
            raise ValueError('Bypass 선택 준비 여부를 확인해 주세요.')
        self.command, self.info, self.cwd, self.emit = command, info, cwd, emit
        self.allow_bypass_permissions = allow_bypass_permissions
        self.choice_helper = choice_helper
        # Validate fork IDs/options before retaining state or launching anything.
        # A fork resumes the source only through Claude's native fork flag. Its
        # displayed/reconnect identity must never fall back to the parent ID.
        if fork_session or new_session_id is not None or type(fork_session) is not bool:
            cli_arguments(command, info, resume, allow_bypass_permissions=allow_bypass_permissions,
                          fork_session=fork_session, new_session_id=new_session_id)
        self._fork_source = str(uuid.UUID(resume)) if fork_session else None
        self._fork_target = str(uuid.UUID(new_session_id)) if fork_session else None
        self._fork_confirmed = False
        self._expected_resume = str(uuid.UUID(resume)) if require_resume_identity else None
        self.session_id = self._fork_target if fork_session else resume
        self.resume_id = None if fork_session else resume
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
        self.seen_text = OrderedDict()
        self.model = ""
        self.original_model = None
        self.model_override = None
        self.effort = None
        self.effort_override = None
        self._effort_reported = False
        self._effort_source = 'unreported'
        self._effort_baselines = {}
        self._effort_applying = False
        self._model_runtime_reported = False
        self._effort_control_supported = True
        self._ultracode_requested = None
        self._runtime_settings_checked = False
        self.permission_mode = ""
        self.original_permission_mode = None
        self.permission_mode_override = None
        self._permission_init_seen = False
        self._permission_modes = help_permission_modes(info.get("help", ""))
        self._bypass_supported = help_bypass_opt_in(info.get('help', ''))
        if self._bypass_supported:
            self._permission_modes.append(BYPASS_MODE)
        self._permission_rejected_modes = set()
        self._permission_control_supported = True
        self._permission_control_verified = False
        self._permission_choices = {}
        self._permission_source = 'unreported'
        self._permission_pending = None
        self.available_models = []
        self.slash_commands = []
        self._commands_reported = False
        self._initialized = False
        self._connection_info = {}
        self._system_commands_reported = False
        self._model_control_supported = True
        self._control_active = False
        self._control_waiters = {}
        self._stream_message_id = None
        self._stream_blocks = {}
        self._stream_index = None
        self._finalized_blocks = OrderedDict()

    @property
    def cleanup_complete(self):
        """True only after this owned child's cleanup has completed successfully."""
        return self.closed and self._close_done.is_set() and self._close_error is None

    @property
    def capabilities(self) -> dict:
        return {"partialMessages": "--include-partial-messages" in self.info.get("help", ""),
                "setModel": bool(self._initialized and self.original_model and
                                 self._model_control_supported and not self.closed),
                "setPermissionMode": bool(self._initialized and self._permission_control_supported
                                          and self._permission_available_modes() and not self.closed),
                "setEffort": bool(self._initialized and self._effort_control_supported
                                  and self._effort_available_levels() and not self.closed)}

    def _effort_available_levels(self):
        rows = [row for row in self.available_models if isinstance(row, dict)
                and self.model and self.model in (row.get('value'), row.get('resolvedModel'))]
        if not rows or rows[0].get('supportsEffort') is False:
            return []
        # Only runtime-reported capabilities for this exact model, never a
        # guessed model-family mapping or the help's global argument choices.
        values = rows[0].get('supportedEffortLevels')
        return [value for value in EFFORT_LEVELS if isinstance(values, list) and value in values]

    def _read_runtime_settings(self):
        """Read only applied model/effort; raw config is never retained/exposed."""
        self._runtime_settings_checked = True
        self._runtime_settings_error = None
        if not any(isinstance(row, dict) and isinstance(row.get('supportedEffortLevels'), list)
                   for row in self.available_models):
            return False
        rid = 'runtime-settings-' + uuid.uuid4().hex
        waiter = {'event': threading.Event(), 'response': None}
        with self.lock:
            self._control_waiters[rid] = waiter
        try:
            self._write({'type': 'control_request', 'request_id': rid,
                         'request': {'subtype': 'get_settings'}})
            if not waiter['event'].wait(CONTROL_TIMEOUT):
                self._runtime_settings_error = 'runtime_settings_timeout'
                return False
            response = waiter['response']
            if self.closed or response is None:
                self._runtime_settings_error = 'connection_closed'
                return False
            if response.get('subtype') != 'success' and self._auth_error(str(response.get('error', ''))):
                self._runtime_settings_error = 'authentication_failed'
                return False
            detail = response.get('response') if isinstance(response, dict) and response.get('subtype') == 'success' else None
            applied = detail.get('applied') if isinstance(detail, dict) else None
            if not isinstance(applied, dict):
                return False
            with self.lock:
                model = applied.get('model')
                if isinstance(model, str) and model and len(model) <= 200 and not any(ord(c) < 32 for c in model):
                    self.model = model
                    self._model_runtime_reported = True
                    if self.original_model is None and self.model_override is None:
                        self.original_model = model
                value = applied.get('effort')
                self._effort_reported = 'effort' in applied and (value is None or value in EFFORT_LEVELS)
                self.effort = value if self._effort_reported else None
                self._effort_source = 'cli-settings' if self._effort_reported else 'unreported'
                if self._effort_reported and self.effort_override is None and not self._effort_applying:
                    self._effort_baselines.setdefault(self.model, value)
                requested = applied.get('ultracodeRequested')
                self._ultracode_requested = requested if type(requested) is bool else None
                return self._effort_reported
        finally:
            with self.lock:
                self._control_waiters.pop(rid, None)

    def _permission_available_modes(self):
        return [mode for mode in self._permission_modes if mode not in self._permission_rejected_modes]

    def _raise_runtime_settings_error(self):
        code = getattr(self, '_runtime_settings_error', None)
        if code:
            self.close()
            raise BridgeError(code, '현재 CLI 설정 응답을 확인하지 못했습니다. 연결과 인증 상태를 확인한 뒤 다시 시도해 주세요.', '연결과 인증 상태 확인')

    def _raise_control_auth_error(self, error):
        if self._auth_error(error):
            self.close()
            raise BridgeError('authentication_failed', '현재 CLI 인증을 확인하지 못했습니다. 기존 Claude 실행 환경의 인증 상태를 확인해 주세요.', '기존 Claude 인증 상태 확인')

    def can_accept_current_control(self, control):
        if self.closed or not self._initialized:
            return False
        if control == 'model':
            return bool(self.model)
        if control == 'effort':
            return bool(self._effort_reported)
        if control == 'permissionMode':
            # This recovery action never substitutes for the bypass warning.
            return bool(self.permission_mode and self.permission_mode != BYPASS_MODE
                        and self._permission_source != 'unreported')
        return False

    def accept_current_control(self, control):
        """Explicitly accept observed CLI state; no CLI request or settings write."""
        with self.lock:
            if self.busy or self.pending or self._control_active:
                raise BridgeError('session_busy', '현재 업무와 확인 요청이 끝난 뒤 설정을 확인해 주세요.', '작업 완료 기다리기')
            if (not self.can_accept_current_control(control) or not self.process
                    or self.process.poll() is not None or self.stopping):
                raise BridgeError('control_current_unavailable', '현재 CLI 값을 확인하지 못해 이전 선택을 유지했습니다.', '연결과 현재 설정 확인')
            attr = {'model': 'model_override', 'effort': 'effort_override', 'permissionMode': 'permission_mode_override'}[control]
            setattr(self, attr, None)
            state = self.model_state()
        self.emit({'model': 'model_changed', 'effort': 'effort_changed', 'permissionMode': 'permission_mode_changed'}[control], state)
        return state

    def _read_runtime_inventory(self):
        """SDK read control only. Never manufacture a prompt to obtain init tools."""
        rid = 'catalog-' + uuid.uuid4().hex
        waiter = {'event': threading.Event(), 'response': None}
        with self.lock:
            self._control_waiters[rid] = waiter
        try:
            self._write({'type': 'control_request', 'request_id': rid, 'request': {'subtype': 'mcp_status'}})
            if not waiter['event'].wait(3):
                return
            envelope = waiter['response'] or {}
            detail = envelope.get('response')
            if envelope.get('subtype') != 'success' or not isinstance(detail, dict):
                return
            servers = detail.get('mcpServers')
            if not isinstance(servers, list):
                return
            # The wire also includes server config, URLs and authentication
            # errors. Keep only display names/status, never those values.
            rows = [{'name': row['name'][:500], 'status': row.get('status', 'unknown')}
                    for row in servers[:1000] if isinstance(row, dict) and isinstance(row.get('name'), str)
                    and isinstance(row.get('status', 'unknown'), str)]
            with self.lock:
                self._connection_info['mcp'] = rows
                self._connection_info.setdefault('reported', {})['mcp'] = True
        finally:
            with self.lock:
                self._control_waiters.pop(rid, None)

    def _bypass_enabled_for_connection(self):
        # An inherited original bypass is existing CLI configuration, not an
        # app opt-in. Display/restore that observed baseline without rewriting it.
        return self.allow_bypass_permissions or self.original_permission_mode == BYPASS_MODE

    def bypass_state(self):
        rejected = BYPASS_MODE in self._permission_rejected_modes
        available = self._bypass_supported and not rejected and self._permission_control_supported
        enabled = self._bypass_enabled_for_connection()
        reason = ('현재 CLI 연결에서 Bypass 전환이 거절되었습니다. 조직 정책과 설치 버전을 확인해 주세요.' if rejected
                  else '설치된 CLI에서 Bypass 선택 준비 옵션을 확인하지 못했습니다.' if not self._bypass_supported
                  else '현재 CLI 연결이 승인 모드 변경을 지원하지 않습니다.' if not self._permission_control_supported
                  else '위험 확인 후 이 업무의 연결을 다시 준비해야 합니다.' if not enabled
                  else '이 연결에서 선택할 수 있습니다. 적용할 때 위험을 확인해야 합니다.')
        return {'available': available, 'enabledForConnection': enabled,
                'requiresReconnect': available and not enabled, 'confirmationRequired': True,
                'active': self.permission_mode == BYPASS_MODE, 'reason': reason}

    def model_state(self) -> dict:
        return {"model": self.model, "modelOverride": self.model_override,
                "availableModels": self.available_models, "capabilities": self.capabilities,
                "effort": self.effort, "effortOverride": self.effort_override,
                "effortSource": self._effort_source,
                "availableEfforts": [{"value": value, "displayName": value,
                                      "description": "현재 모델과 연결에서 제공한 수준"}
                                     for value in self._effort_available_levels()],
                "effortSupport": ("unavailable" if not self.capabilities['setEffort'] else
                                  "confirmed" if self._effort_reported else "unverified"),
                "effortChangeRequiresReconnect": False,
                "effortResetRequiresReconnect": False,
                "effortResetAvailable": self._effort_baselines.get(self.model) in self._effort_available_levels(),
                "permissionMode": self.permission_mode,
                "permissionModeLabel": mode_label(self.permission_mode),
                "permissionModeSource": self._permission_source,
                "permissionModeOverride": self.permission_mode_override,
                "bypassPermissions": self.bypass_state(),
                "availablePermissionModes": mode_options(self._permission_available_modes(), include_bypass=True),
                "permissionModeCycle": mode_cycle(self._permission_available_modes()),
                "permissionModeSupport": ("unavailable" if not self.capabilities["setPermissionMode"] else
                                          "confirmed" if self._permission_control_verified else "unverified"),
                "permissionModeResetRequiresReconnect": False,
                "permissionModeResetAvailable": (self.original_permission_mode is not None
                    and self._permission_control_supported
                    and mode_wire_value(self.original_permission_mode, self._permission_available_modes()) is not None)}

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
                self._raise_control_auth_error(error)
                if any(word in error for word in ("unsupported", "unknown request", "unknown subtype")):
                    self._model_control_supported = False
                raise BridgeError("model_rejected", "현재 연결에서 모델 변경이 거절되었습니다. 회사에서 사용할 수 있는 모델 이름과 연결 상태를 확인해 주세요.", "모델 이름과 연결 확인")
            with self.lock:
                self.model, self.model_override = selected, model
                self.effort, self._effort_reported = None, False
                self._effort_source = 'unreported'
            self._read_runtime_settings()
            self._raise_runtime_settings_error()
            with self.lock:
                state = self.model_state()
            self.emit("model_changed", state)
            return state
        finally:
            with self.lock:
                self._control_waiters.pop(rid, None)
                self._control_active = False

    def set_effort(self, effort: str | None) -> dict:
        """Apply session-only effort with SDK control and effective readback.

        Reset restores the observed pre-change level on this model. It never
        retires a connection or clears unrelated model/permission selections.
        """
        if effort is not None and (not isinstance(effort, str) or effort not in EFFORT_LEVELS):
            raise BridgeError('effort_invalid', '현재 연결에서 제공한 추론 수준을 선택해 주세요.', '추론 수준 확인')
        with self.lock:
            if self.busy or self.pending or self._control_active:
                raise BridgeError('session_busy', '현재 업무와 확인 요청이 끝난 뒤 추론 수준을 변경해 주세요.', '작업 완료 기다리기')
            if self.closed or not self._initialized or not self.process or self.process.poll() is not None:
                raise BridgeError('effort_unavailable', '업무 연결이 준비된 뒤 추론 수준을 변경할 수 있습니다.', '연결 상태 확인')
            self._control_active = True
        rid = None
        try:
            if effort is None:
                selected = self._effort_baselines.get(self.model)
                if selected not in self._effort_available_levels():
                    raise BridgeError('effort_reset_unavailable', '현재 모델의 변경 전 추론 수준을 확인하지 못해 복원하지 않았습니다. 다른 모델·승인 설정은 유지합니다.', '현재 연결에서 제공한 추론 수준 선택')
            else:
                selected = effort
            if not self._read_runtime_settings() or self._ultracode_requested is None:
                self._raise_runtime_settings_error()
                raise BridgeError('effort_unavailable', '현재 CLI가 실제 추론 설정을 제공하지 않아 안전하게 변경할 수 없습니다.', '원본 CLI에서 확인')
            if not self.capabilities['setEffort'] or selected not in self._effort_available_levels():
                raise BridgeError('effort_invalid', '현재 모델에서 제공한 추론 수준을 선택해 주세요.', '모델과 추론 수준 확인')
            rid = 'effort-' + uuid.uuid4().hex
            waiter = {'event': threading.Event(), 'response': None}
            with self.lock:
                self._control_waiters[rid] = waiter
                self._effort_applying = True
            self._write({'type': 'control_request', 'request_id': rid,
                         'request': {'subtype': 'apply_flag_settings',
                                     'settings': {'effortLevel': selected, 'ultracode': self._ultracode_requested}}})
            if not waiter['event'].wait(CONTROL_TIMEOUT):
                self.close()
                raise BridgeError('effort_timeout', '추론 수준 변경 응답을 확인하지 못해 연결을 종료했습니다. 다음 요청은 기존 설정을 사용합니다.', '다음 요청으로 다시 연결')
            response = waiter['response']
            if self.closed or response is None:
                raise BridgeError('connection_closed', '추론 수준을 변경하기 전에 업무 연결이 종료되었습니다.', '다음 요청으로 다시 연결')
            if response.get('subtype') != 'success':
                error = str(response.get('error', '')).casefold()
                self._raise_control_auth_error(error)
                if any(word in error for word in ('unsupported', 'unknown request', 'unknown subtype')):
                    self._effort_control_supported = False
                raise BridgeError('effort_rejected', '현재 연결에서 추론 수준 변경이 거절되었습니다. 기존 설정을 유지합니다.', '회사 정책과 연결 상태 확인')
            self.effort, self._effort_reported = None, False
            self._effort_source = 'unreported'
            if not self._read_runtime_settings():
                self._raise_runtime_settings_error()
                self.close()
                raise BridgeError('effort_unconfirmed', '변경 후 실제 추론 수준을 확인하지 못해 연결을 종료했습니다. 다음 요청은 기존 설정을 사용합니다.', '다음 요청으로 다시 연결')
            with self.lock:
                if self.closed:
                    raise BridgeError('connection_closed', '추론 수준 확인 중 업무 연결이 종료되었습니다.', '다음 요청으로 다시 연결')
                self.effort_override = effort
                state = self.model_state()
            self.emit('effort_changed', state)
            return state
        finally:
            with self.lock:
                if rid:
                    self._control_waiters.pop(rid, None)
                self._effort_applying = False
                self._control_active = False

    def set_permission_mode(self, mode: str | None) -> dict:
        """Apply one live connection mode only after the CLI acknowledges it.

        Help proves that a mode name exists, not that policy will accept a live
        change. Missing original state is unavailable, never an invented alias
        or a reconnection that discards another selected setting.
        """
        if mode is not None and mode_wire_value(mode, self._permission_available_modes()) is None:
            raise BridgeError("permission_mode_invalid", "현재 연결에서 선택할 수 있는 승인 모드를 확인해 주세요.", "승인 모드 확인")
        with self.lock:
            if mode is not None and mode_wire_value(mode, self._permission_available_modes()) is None:
                raise BridgeError("permission_mode_invalid", "현재 연결에서 선택할 수 있는 승인 모드를 확인해 주세요.", "승인 모드 확인")
            if self.busy or self.pending or self._control_active:
                raise BridgeError("session_busy", "현재 작업과 확인 요청이 끝난 뒤 승인 모드를 변경해 주세요.", "작업 완료 기다리기")
            if self.closed or not self._initialized or not self.process or self.process.poll() is not None:
                raise BridgeError("permission_mode_unavailable", "업무 연결이 준비된 뒤 승인 모드를 변경할 수 있습니다.", "연결 상태 확인")
            if mode is not None and not self.capabilities["setPermissionMode"]:
                raise BridgeError("permission_mode_unavailable", "현재 연결에서 승인 모드 변경을 확인하지 못했습니다.", "연결 상태 확인")
            original_wire = mode_wire_value(self.original_permission_mode, self._permission_available_modes())
            if mode is None and (original_wire is None or not self._permission_control_supported):
                raise BridgeError('permission_mode_reset_unavailable', '변경 전 승인 모드의 복원을 확인할 수 없습니다. 현재 모델·추론 수준·승인 모드는 그대로 유지합니다.', '현재 연결에서 제공한 승인 모드 선택')
            selected = mode_wire_value(mode, self._permission_available_modes()) if mode is not None else original_wire
            if selected == BYPASS_MODE and not self._bypass_enabled_for_connection():
                raise BridgeError('bypass_opt_in_required', 'Bypass는 위험 확인 후 이 업무의 연결을 다시 준비해야 합니다. 현재 승인 모드는 유지했습니다.', 'Bypass 위험 확인')
            rid = "permission-mode-" + uuid.uuid4().hex
            previous_override = self.permission_mode_override
            waiter = {"event": threading.Event(), "response": None}
            self._control_active = True
            self._permission_pending = {'reported': None}
            if selected is not None:
                self._control_waiters[rid] = waiter
        try:
            self._write({"type": "control_request", "request_id": rid,
                         "request": {"subtype": "set_permission_mode", "mode": selected}})
            if not waiter["event"].wait(CONTROL_TIMEOUT):
                self.close()
                raise BridgeError("permission_mode_timeout", "승인 모드 변경 응답을 확인하지 못해 업무 연결을 종료했습니다. 다음 요청에서 기존 설정으로 다시 연결합니다.", "다음 요청으로 다시 연결")
            response = waiter["response"]
            if self.closed or response is None:
                raise BridgeError("connection_closed", "승인 모드를 변경하기 전에 업무 연결이 종료되었습니다.", "다음 요청으로 다시 연결")
            if response.get("subtype") != "success":
                error = str(response.get("error", "")).casefold()
                self._raise_control_auth_error(error)
                with self.lock:
                    self._permission_rejected_modes.add(selected)
                    if any(word in error for word in ("unknown request", "unknown subtype", "unsupported control")):
                        self._permission_control_supported = False
                raise BridgeError("permission_mode_rejected", "현재 연결에서 승인 모드 변경이 거절되었습니다. 현재 모드를 유지하며 거절된 선택지는 숨겼습니다.", "회사 정책과 연결 상태 확인")
            with self.lock:
                if self.closed:
                    raise BridgeError("connection_closed", "승인 모드를 변경하기 전에 업무 연결이 종료되었습니다.", "다음 요청으로 다시 연결")
                detail = response.get('response')
                acknowledged = observed_mode(detail.get('mode')) if isinstance(detail, dict) else ''
                reported = self._permission_pending['reported']
                self._permission_pending = None
                self.permission_mode, self.permission_mode_override = acknowledged or reported or selected, mode
                bypass_declined = selected == BYPASS_MODE and self.permission_mode != BYPASS_MODE
                if bypass_declined:
                    # A successful control envelope can still report a policy-
                    # clamped mode. Never retain/replay a bypass that did not apply.
                    self._permission_rejected_modes.add(BYPASS_MODE)
                    self.permission_mode_override = previous_override
                self._permission_source = 'cli-control' if acknowledged or not reported else 'cli-status'
                self._permission_control_verified = True
                # A change before system/init means the original mode was never
                # observed. A later init must not mistake our override for it.
                self._permission_init_seen = True
                state = self.model_state()
            self.emit("permission_mode_changed", state)
            if bypass_declined:
                raise BridgeError('permission_mode_rejected', 'CLI가 Bypass 대신 다른 승인 모드를 보고했습니다. 실제 모드를 표시하며 Bypass 선택은 적용하지 않았습니다.', '회사 정책과 연결 상태 확인')
            return state
        finally:
            with self.lock:
                self._control_waiters.pop(rid, None)
                self._permission_pending = None
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

    def _is_expected_stop_result(self, data, previous_session):
        # Only the observed user-interrupt diagnostic is benign. A marker
        # embedded in another error, an unknown signature or another session
        # must still use the normal failure/recovery path.
        if (not self.stopping or not previous_session
                or self._fork_source and not self._connection_info.get('sessionId')
                or data.get('session_id') not in (None, previous_session)):
            return False
        errors = data.get('errors', [])
        if not isinstance(errors, list):
            return False
        details = [value for value in [data.get('result', ''), *errors] if value != '']
        expected = {'result_type=user', 'last_content_type=n/a', 'stop_reason=tool_use'}
        return bool(details) and all(
            isinstance(value, str) and len(parts := value.split()) == 4
            and parts[0] == '[ede_diagnostic]' and set(parts[1:]) == expected
            for value in details)

    def start(self):
        with self.lock:
            if self.closed or self.stopping:
                raise ValueError("중지된 연결은 시작하지 않습니다.")
            if self.process is not None:
                if self.process.poll() is not None:
                    raise ValueError("CLI 연결이 종료되었습니다. 다시 연결해 주세요.")
                return
            env = dict(os.environ)
            # Encoding and a UI presentation signal only; Claude-owned settings stay untouched.
            env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", COMPANY_WORKSPACE_UI="1")
            self.process = subprocess.Popen(cli_arguments(self.command, self.info, self._fork_source or self.session_id,
                allow_bypass_permissions=self.allow_bypass_permissions,
                fork_session=self._fork_source is not None, new_session_id=self._fork_target),
                cwd=self.cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=HIDDEN)
            self._readers = [threading.Thread(target=self._read, daemon=True),
                             threading.Thread(target=self._read_stderr, daemon=True)]
            for reader in self._readers:
                reader.start()
            self._write({"type": "control_request", "request_id": self.initialize_id,
                         "request": {"subtype": "initialize"}})

    def connection_state(self):
        """Only metadata the live child has reported, including pre-turn commands."""
        with self.lock:
            return {**self._connection_info, "sessionId": self.session_id,
                    **({'forkConfirmed': self._fork_confirmed} if self._fork_source else {}),
                    **self.model_state(), "slashCommands": list(self.slash_commands),
                    "reported": {"skills": False, "plugins": False, "mcp": False,
                                 "tools": False, **self._connection_info.get("reported", {}),
                                 "commands": self._commands_reported}}

    def prepare(self):
        """Initialize the existing CLI without sending a user/model request.

        Its normal startup hooks and MCP discovery still run. The caller must
        have confirmed trust in the task's folder before invoking this method.
        """
        with self.lock:
            if self.busy or self.pending or self._control_active:
                raise ValueError("현재 업무와 연결 준비가 끝난 뒤 다시 시도해 주세요.")
            self._control_active = True
        try:
            self.start()
            if not self.ready.wait(PREPARE_TIMEOUT):
                raise ValueError("CLI 명령 준비 응답이 60초 동안 없습니다. 시작 후크·MCP 상태를 확인한 뒤 다시 연결해 주세요.")
            if self.initialization_error:
                raise ValueError(self.initialization_error)
            if self.closed or self.stopping or not self.process or self.process.poll() is not None:
                raise ValueError("명령 목록을 준비하는 동안 CLI 연결이 종료되었습니다. 기존 Claude 실행 환경을 확인해 주세요.")
            if not self._runtime_settings_checked:
                self._read_runtime_settings()
                self._raise_runtime_settings_error()
            self._read_runtime_inventory()
            return self.connection_state()
        except Exception:
            self.close()
            raise
        finally:
            with self.lock:
                self._control_active = False

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
            self._begin_tool_activity_turn()
            self.busy = True
            self.last_result = None
            self.seen_text.clear()
            self._stream_blocks.clear()
            self._finalized_blocks.clear()
            self._stream_message_id = self._stream_index = None
            self._assistant_frames = OrderedDict()
            self._stream_frames = set()
            self._choice_tools = set()
            self._choice_questions = set()
            self._hook_states = {}
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
            # Announce activity before writing: a fast result must not be
            # followed by a delayed local "running" event for the same turn.
            self.emit("status", {"state": "running", "label": "요청을 처리하고 있어요"})
            self._write({"type": "user", "message": {"role": "user", "content": prompt},
                         "session_id": self.session_id or "", "parent_tool_use_id": None})
        except Exception as exc:
            if not self.stopping and not self.closed:
                self._progress_error(str(exc))
                self._interrupt_executions()
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
                self._progress_error(str(exc))
                self._interrupt_executions()
                self.emit("error", {"message": str(exc)})
        finally:
            if not self.closed and not self.stopping:
                self._progress_error('CLI 연결이 종료되었습니다. 로그인 또는 실행 환경을 확인해 주세요.')
                self._interrupt_executions()
                self.emit("error", {"message": "CLI 연결이 종료되었습니다. 로그인 또는 실행 환경을 확인해 주세요."})
            self.close()
            self.process.stdout.close()

    def _handle_stream(self, data: dict):
        # A delegated agent's text must never become the coordinator's answer.
        if data.get("parent_tool_use_id"):
            return
        # Replays are identifiable only when the transport supplies an ID.
        # Repeated equal deltas without IDs can be valid text and stay intact.
        frame_id = data.get('uuid')
        if isinstance(frame_id, str):
            seen = getattr(self, '_stream_frames', None)
            if seen is None:
                seen = self._stream_frames = set()
            if frame_id in seen:
                return
            if len(seen) < 10000:
                seen.add(frame_id)
        event = data.get("event", {})
        kind = event.get("type")
        if kind == "message_start":
            identifier = event.get("message", {}).get("id")
            self._stream_message_id = identifier if isinstance(identifier, str) and 0 < len(identifier) <= 200 else "stream-" + uuid.uuid4().hex
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
            digest = self._stream_blocks.get(key)
            if digest is None:
                digest = hashlib.sha256()
                _remember_evidence(self._stream_blocks, key, digest)
            digest.update(text.encode('utf-8', errors='replace'))
            if text:
                self.emit("assistant_delta", {"messageId": key[0], "index": index, "text": text})

    def _assistant_text(self, text: str, message_id=None, index=0):
        if not isinstance(text, str) or not text:
            return
        if not isinstance(message_id, str) or not 0 < len(message_id) <= 200:
            message_id = None
        digest = _text_fingerprint(text)
        frames = getattr(self, '_assistant_frames', None)
        if frames is None:
            frames = self._assistant_frames = OrderedDict()
        identity = (message_id, index, digest)
        if message_id is not None and identity in frames:
            return
        # Complete messages may contain a single block even when the raw stream
        # block index is greater than zero. Match that block to its streamed text.
        matches = [key for key, value in self._stream_blocks.items()
                   if value.digest() == digest and (message_id is None or key[0] == message_id)]
        key = next((key for key in matches if key not in self._finalized_blocks), None)
        if key is None and matches:
            return
        if key is None:
            active_key = (message_id, self._stream_index)
            key = (active_key if active_key in self._stream_blocks and active_key not in self._finalized_blocks
                   else (message_id or "message-" + uuid.uuid4().hex, index))
            if message_id is None and digest in self.seen_text:
                return
            # Without raw stream events, separate single-block assistant frames
            # share a message ID and each enumerates at zero. Give later blocks
            # a stable free index instead of losing their text.
            while key in self._finalized_blocks:
                key = (key[0], key[1] + 1)
        _remember_evidence(self._finalized_blocks, key)
        if message_id is not None:
            _remember_evidence(frames, identity)
        _remember_evidence(self.seen_text, digest)
        self.emit("assistant", {"messageId": key[0], "index": key[1], "text": text})

    @staticmethod
    def _command_details(names, detail):
        descriptions = {row.get('name'): row for row in detail
                        if isinstance(row, dict) and isinstance(row.get('name'), str)}
        return [descriptions.get(row, row) if isinstance(row, str)
                else {**descriptions.get(row.get('name') if isinstance(row.get('name'), str) else '', {}), **row}
                if isinstance(row, dict) else row for row in names]

    def _execution_capture(self):
        # Lazy initialization also supports small protocol-only test fixtures.
        with self.lock:
            capture = getattr(self, '_execution_capture_state', None)
            if capture is None:
                from .executions import ExecutionCapture
                capture = self._execution_capture_state = ExecutionCapture(lambda value: self.emit('execution', value))
                if getattr(self, '_execution_closed', False):
                    capture.interrupt(closed=True)
            return capture

    def _progress_error(self, message):
        capture = getattr(self, '_progress_capture_state', None)
        if capture is not None and self.busy:
            try:
                capture.error(message)
            except Exception:
                pass

    def _interrupt_executions(self, *, closed=False, activity=True):
        if closed:
            self._execution_closed = True
        capture = getattr(self, '_execution_capture_state', None)
        if capture is not None:
            capture.interrupt(closed=closed)
        if activity:
            live = getattr(self, '_run_activity_state', None)
            if live is not None:
                live.finish()
            progress = getattr(self, '_progress_capture_state', None)
            if progress is not None:
                progress.finish()
            self._tool_activity_finished = True
            capture = getattr(self, '_tool_activity_state', None)
            if capture is not None:
                capture.interrupt()

    def _begin_tool_activity_turn(self):
        live = getattr(self, '_run_activity_state', None)
        if live is not None:
            live.finish()
        progress = getattr(self, '_progress_capture_state', None)
        if progress is not None:
            progress.finish()
        from .progress_log import ProgressCapture
        from collections import OrderedDict
        from .tool_activity import RunActivityCapture
        live_tombstones = getattr(self, '_run_activity_tombstones', None)
        if live_tombstones is None:
            live_tombstones = self._run_activity_tombstones = OrderedDict()
        self._run_activity_state = RunActivityCapture(
            lambda value: self.emit('run_activity', value),
            run_id=getattr(self, '_tool_activity_run_id', None), tombstones=live_tombstones)
        tombstones = getattr(self, '_progress_tombstones', None)
        if tombstones is None:
            tombstones = self._progress_tombstones = OrderedDict()
        self._progress_capture_state = ProgressCapture(
            lambda value: self.emit('progress_record', value),
            run_id=getattr(self, '_tool_activity_run_id', None), tombstones=tombstones)
        old = getattr(self, '_tool_activity_state', None)
        if old is not None:
            old.interrupt()
        self._tool_activity_finished = False
        self._tool_activity_state = None

    def _tool_activity_capture(self):
        with self.lock:
            capture = getattr(self, '_tool_activity_state', None)
            if capture is None:
                from .tool_activity import ToolActivityCapture
                tombstones = getattr(self, '_tool_activity_tombstones', None)
                if tombstones is None:
                    tombstones = self._tool_activity_tombstones = (set(), deque())
                capture = self._tool_activity_state = ToolActivityCapture(
                    lambda value: self.emit('tool_activity', value),
                    run_id=getattr(self, '_tool_activity_run_id', None), tombstones=tombstones)
                if getattr(self, '_tool_activity_finished', False) or getattr(self, '_execution_closed', False):
                    capture.interrupt()
            return capture

    def handle(self, data: dict):
        if self.closed:
            return
        kind = data.get("type")
        if self._expected_resume and not data.get('parent_tool_use_id'):
            native_id = data.get('session_id')
            if native_id is not None or kind == 'system' and data.get('subtype') == 'init':
                try:
                    accepted = str(uuid.UUID(native_id)) == self._expected_resume
                except (ValueError, TypeError, AttributeError):
                    accepted = False
                if not accepted:
                    self.initialization_error = 'Claude가 원래 대화의 세션 ID를 확인해 주지 않았습니다. 새 대화로 대신 이어가지 않았어요. 원래 환경과 세션 기록을 확인해 주세요.'
                    self.ready.set()
                    self.close()
                    self.emit('error', {'code': 'resume_identity', 'message': self.initialization_error,
                                        'nextAction': '원래 Claude 세션 확인'})
                    return
        if self._fork_source and not data.get('parent_tool_use_id'):
            native_id = data.get('session_id')
            has_identity = native_id is not None
            needs_identity = kind in {'assistant', 'result'} or kind == 'system' and data.get('subtype') == 'init'
            if has_identity or needs_identity and not self._fork_confirmed:
                try:
                    from .session_import import session_uuid
                    accepted = session_uuid(native_id) == self._fork_target
                except ValueError:
                    accepted = False
                if not accepted:
                    self.initialization_error = 'Claude가 원본과 다른 분기 세션 ID를 확인해 주지 않았습니다. 원본 대화는 이어서 실행하지 않았습니다.'
                    self.ready.set()
                    self.close()
                    self.emit('error', {'code': 'fork_identity', 'message': self.initialization_error,
                                        'nextAction': '원본 업무에서 대화 분기 다시 만들기'})
                    return
                self._fork_confirmed = True
                self.resume_id = self._fork_target
        # Detail projection is tied to a submitted root request. Preparing a
        # connection or changing its settings must not create activity history.
        progress = getattr(self, '_progress_capture_state', None)
        if progress is not None and self.busy and not self.stopping:
            try:
                progress.handle(data)
            except Exception:
                pass  # Recording detail must never interrupt the CLI reader.
        live = getattr(self, '_run_activity_state', None)
        if live is not None and self.busy and not self.stopping:
            try:
                live.handle(data)
            except Exception:
                pass
        if kind == "control_response":
            response = data.get("response", {})
            if response.get("request_id") == self.initialize_id:
                if response.get("subtype") == "error":
                    self.initialization_error = "CLI 대화 초기화가 거절되었습니다. 설치 버전을 확인해 주세요."
                else:
                    self._initialized = True
                    detail = response.get("response", {})
                    if isinstance(detail, dict):
                        for name, field in (('tools', 'tools'), ('mcp', 'mcp_servers'), ('skills', 'skills'), ('plugins', 'plugins')):
                            if isinstance(detail.get(field), list):
                                self._connection_info[name] = detail[field]
                                self._connection_info.setdefault('reported', {})[name] = True
                        if isinstance(detail.get("commands"), list):
                            self.slash_commands = (self._command_details(self.slash_commands, detail["commands"])
                                                   if self._system_commands_reported else detail["commands"])
                            self._commands_reported = True
                        if isinstance(detail.get("models"), list):
                            self.available_models = detail["models"]
                        reported_mode = observed_mode(detail.get('current_permission_mode'))
                        if reported_mode and self._permission_source == 'unreported':
                            self.permission_mode = self.original_permission_mode = reported_mode
                            self._permission_init_seen = True
                            self._permission_source = 'cli-initialize'
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
                permission_choices, permission_updates = session_choices(request)
                self._permission_choices[rid] = permission_updates
            self.emit("request", {"id": rid, "tool": request.get("tool_name", ""),
                "input": request.get("input", {}), "description": request.get("description", ""),
                "title": request.get("title", ""), "agent": request.get("agent_id"),
                "permissionChoices": permission_choices, **request_context(request)})
        elif kind == "control_cancel_request":
            with self.lock:
                self.pending.pop(data.get("request_id"), None)
                self._permission_choices.pop(data.get("request_id"), None)
            self.emit("request_closed", {"id": data.get("request_id")})
        elif kind == "system":
            subtype = data.get("subtype")
            if subtype == "init":
                self.session_id = data.get("session_id") or self.session_id
                if not self._model_runtime_reported and self.model_override is None:
                    self.model = data.get("model", "")
                if self.original_model is None and self.model:
                    self.original_model = self.model
                reported_mode = observed_mode(data.get("permissionMode"))
                if not self._permission_init_seen:
                    self._permission_init_seen = True
                    self.original_permission_mode = reported_mode or None
                if reported_mode and self._permission_source == 'unreported' and self._permission_pending is None:
                    self.permission_mode = reported_mode
                    self._permission_source = 'cli-init'
                if 'effort' in data and not self._effort_reported and self.effort_override is None:
                    value = data.get('effort')
                    self._effort_reported = value is None or value in EFFORT_LEVELS
                    self.effort = value if self._effort_reported else None
                    self._effort_source = 'cli-init' if self._effort_reported else 'unreported'
                    if self._effort_reported and not self._effort_applying:
                        self._effort_baselines.setdefault(self.model, value)
                if isinstance(data.get("slash_commands"), list):
                    # system/init is authoritative for which names are listed;
                    # initialize often supplies richer descriptions for them.
                    self.slash_commands = self._command_details(data["slash_commands"], self.slash_commands)
                    self._system_commands_reported = True
                    self._commands_reported = True
                self._connection_info = {"sessionId": self.session_id, **self.model_state(),
                    **({'forkConfirmed': self._fork_confirmed} if self._fork_source else {}),
                    "slashCommands": self.slash_commands,
                    "skills": data.get("skills", []), "plugins": data.get("plugins", []),
                    "mcp": data.get("mcp_servers", []), "tools": data.get("tools", []),
                    "reported": {"commands": self._commands_reported,
                                 **{name: isinstance(data.get(field), list) for name, field in
                                    (("skills", "skills"), ("plugins", "plugins"),
                                     ("mcp", "mcp_servers"), ("tools", "tools"))}}}
                self.emit("connected", self._connection_info)
            elif subtype == "task_started" and data.get("task_type") in {"local_agent", "local_workflow"}:
                self.tasks.add(data.get("task_id"))
                self.emit("status", {"state": "running", "label": "담당 작업자가 처리하고 있어요"})
            elif subtype == "task_notification" or (subtype == "task_updated" and
                    data.get("patch", {}).get("status") in {"completed", "failed", "stopped"}):
                self.tasks.discard(data.get("task_id"))
                # Do not announce completion yet; the coordinator still needs its next result.
            elif subtype in {'hook_started', 'hook_response'}:
                from .hook_status import project as hook_status
                status = hook_status(data)
                if status:
                    states = getattr(self, '_hook_states', None)
                    if states is None:
                        states = self._hook_states = {}
                    name = data.get('hook_name') or data.get('hook_id') or 'Stop'
                    if isinstance(name, str) and len(name) <= 500 and len(states) < 100:
                        states[name] = status
                    self.emit('verification', status)
            elif subtype == "status":
                # CLI uses status:null for permission-mode changes. This is
                # settings metadata, not proof that a user request is running.
                reported_mode = observed_mode(data.get('permissionMode'))
                state = None
                with self.lock:
                    if reported_mode:
                        if self._permission_pending is not None:
                            self._permission_pending['reported'] = reported_mode
                        else:
                            self.permission_mode = reported_mode
                            self._permission_source = 'cli-status'
                            state = self.model_state()
                    active = self.busy and not self.pending
                if state is not None:
                    self.emit('permission_mode_changed', state)
                if active and data.get('status') == 'compacting':
                    self.emit("status", {"state": "running", "label": "대화 내용을 정리하고 있어요"})
        elif kind == "stream_event":
            self._handle_stream(data)
        elif kind == 'tool_progress':
            self._tool_activity_capture().progress(data)
        elif kind == "assistant":
            if data.get("error") == "authentication_failed":
                self._authentication_failed()
                return
            if not data.get('parent_tool_use_id'):
                self.session_id = data.get("session_id") or self.session_id
            parent = data.get("parent_tool_use_id")
            message = data.get("message", {})
            blocks = message.get('content', []) if isinstance(message, dict) else []
            for index, block in enumerate(blocks if isinstance(blocks, list) else []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and not parent:
                    self._assistant_text(block.get("text", ""), message.get("id"), index)
                elif block.get("type") == "tool_use":
                    self._tool_activity_capture().request(block, parent=parent)
                    if not parent and isinstance(block.get('name'), str) and block['name'] in {'Bash', 'PowerShell'}:
                        self._execution_capture().request(block)
                        inputs = block.get('input') if isinstance(block.get('input'), dict) else {}
                        command = inputs.get('command')
                        tool_id = block.get('id')
                        if isinstance(tool_id, str) and isinstance(command, str) and 'html-choices' in command:
                            from .choices import command_is_helper
                            try:
                                # Legacy adapters may explicitly inject an origin. The
                                # generic app never discovers or invokes a harness.
                                origin = self.choice_helper() if self.choice_helper else None
                                accepted = command_is_helper(command, origin)
                            except (ValueError, OSError, KeyError, TypeError, UnicodeError):
                                accepted = False
                            choices = getattr(self, '_choice_tools', None)
                            if choices is None:
                                choices = self._choice_tools = set()
                            if accepted and len(choices) < 100:
                                choices.add(tool_id)
                    self.emit("activity", {"tool": block.get("name"), "id": block.get("id"),
                        "skill": block.get("input", {}).get("skill") if block.get("name") == "Skill" and isinstance(block.get('input'), dict) else None})
        elif kind == 'user':
            from .choices import from_tool_output
            parent = data.get('parent_tool_use_id')
            message = data.get('message')
            blocks = message.get('content', []) if isinstance(message, dict) else []
            if isinstance(blocks, list):
                for block in blocks:
                    if not isinstance(block, dict) or block.get('type') != 'tool_result':
                        continue
                    self._tool_activity_capture().result(block, parent=parent)
                    if parent:
                        continue
                    self._execution_capture().result(block)
                    if (block.get('is_error') or not isinstance(block.get('tool_use_id'), str)
                            or block.get('tool_use_id') not in getattr(self, '_choice_tools', set())):
                        continue
                    choice = from_tool_output(block.get('content'))
                    if choice:
                        seen = getattr(self, '_choice_questions', None)
                        if seen is None:
                            seen = self._choice_questions = set()
                        if choice['id'] not in seen:
                            seen.add(choice['id'])
                            self.emit('choice', choice)
        elif kind == "result":
            if data.get('parent_tool_use_id'):
                return
            self._interrupt_executions(activity=bool(data.get('is_error') or not self.tasks))
            self.last_result = data
            previous_session = self.session_id
            self.session_id = data.get("session_id") or self.session_id
            text = data.get("result", "")
            error_text = "; ".join(map(str, data.get("errors", []))) or text
            if data.get("is_error") and (self._auth_error(error_text)
                                        or isinstance(text, str) and self._auth_error(text)):
                self._authentication_failed()
                return
            if text and _text_fingerprint(text) not in self.seen_text and not data.get('is_error'):
                self._assistant_text(text)
            if data.get("is_error"):
                self.busy = False
                # A terminal failure cannot keep obsolete permission cards or
                # delegated tasks alive for the next user turn.
                with self.lock:
                    pending = list(self.pending)
                    self.pending.clear()
                    self._permission_choices.clear()
                    self.tasks.clear()
                for rid in pending:
                    self.emit('request_closed', {'id': rid})
                diagnostic = '[ede_diagnostic]' in error_text
                if diagnostic and self.session_id:
                    self.resume_id = self.session_id
                if self._is_expected_stop_result(data, previous_session):
                    # Keep last_result for diagnosis without an error banner,
                    # failed notification or completed-result event. Report
                    # stopped only after _finish_stop confirms owned CLI exit.
                    return
                self.emit("error", {"code": 'cli_turn_incomplete' if diagnostic else "task_failed",
                    "nextAction": '현재 결과를 확인한 뒤 같은 대화에서 후속 요청' if diagnostic else "자료와 연결 상태를 확인하고 다시 요청",
                    **({'resumeSessionId': self.resume_id, 'diagnosticCode': 'ede_diagnostic'} if diagnostic else {}),
                    "message": ('Claude가 도구 처리 뒤 최종 답변을 완료하지 못했어요. 지금까지의 대화와 파일 변경은 남아 있습니다. '
                                '결과를 확인한 뒤 같은 대화에서 계속 요청할 수 있어요. 이전 요청을 자동으로 재실행하지 않습니다. '
                                '(진단: ede_diagnostic)') if diagnostic else error_text or "작업을 완료하지 못했습니다."})
            elif not self.tasks:
                self.busy = False
                self.resume_id = self.session_id
                from .hook_status import at_result
                problems = [state for state in getattr(self, '_hook_states', {}).values()
                            if state.get('state') == 'needs-review']
                verification = at_result(problems[-1] if problems else None)
                self.emit("result", {"sessionId": self.session_id, "durationMs": data.get("duration_ms"),
                    "usage": data.get("usage", {}), "costUsd": data.get("total_cost_usd"),
                    "verification": verification})
            else:
                self.emit("status", {"state": "running", "label": "작업자의 결과를 기다리고 있어요"})

    def respond(self, rid: str, allow: bool, answers=None, permission_choice_id=None):
        with self.lock:
            request = self.pending.get(rid)
            if request is None:
                raise ValueError("이미 처리되었거나 만료된 질문입니다.")
            permission_update = None
            if permission_choice_id is not None:
                if (not allow or request.get("tool_name") == "AskUserQuestion"
                        or request.get('suppress_always_allow_rule') is True
                        or not isinstance(permission_choice_id, str)
                        or permission_choice_id not in self._permission_choices.get(rid, {})):
                    raise ValueError("이 요청에서 제공된 승인 범위를 선택해 주세요.")
                permission_update = self._permission_choices[rid][permission_choice_id]
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
            if permission_update is not None:
                response["updatedPermissions"] = [permission_update]
            self._write({"type": "control_response", "response": {"subtype": "success",
                        "request_id": rid, "response": response}})
            del self.pending[rid]
            self._permission_choices.pop(rid, None)
        self.emit("request_closed", {"id": rid})

    def close(self):
        self._interrupt_executions(closed=True)
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
                self._permission_choices.clear()
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
        self._interrupt_executions(closed=True)
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
