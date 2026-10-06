"""Locate the active installer registration, then call its typed local service.

Never import another installation into this process, invent a state directory,
or launch model work. The core re-resolves the registration on every operation.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import stat
import subprocess

if __package__:
    from .owned_process import OutputLimitExceeded, run_owned
else:
    from owned_process import OutputLimitExceeded, run_owned

HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

DIAGNOSTIC_MESSAGES = {
    'ready': '현재 범위의 Company Agent 설치와 관리 연결 파일을 확인했습니다.',
    'registration_missing': 'Company Agent 설치 등록이 없습니다. 일반 스킬 메타데이터는 별도로 확인합니다.',
    'registration_invalid': 'Company Agent 설치 등록의 형식 또는 읽기 상태를 확인하지 못했습니다.',
    'registration_disabled': 'Company Agent 설치 등록이 비활성 상태입니다.',
    'config_mismatch': '설치에 등록된 Claude 설정 범위와 현재 앱의 설정 범위가 다릅니다.',
    'scope_mismatch': '선택한 폴더에 적용되는 Company Agent 설치 등록이 없습니다.',
    'plugin_disabled': '현재 범위에서 Company Agent 플러그인이 활성화되어 있지 않습니다.',
    'plugin_registry_invalid': 'Claude의 플러그인 설치 목록을 읽지 못했습니다.',
    'version_mismatch': 'Company Agent 등록 버전과 Claude의 플러그인 설치 버전이 일치하지 않습니다.',
    'registration_ambiguous': '같은 범위의 설치 정보가 겹칩니다. 임의로 선택하지 않았습니다.',
    'runtime_unavailable': '설치에 등록된 실행 경로를 확인하지 못했습니다.',
    'api_unsupported': '이 Company Agent 설치에는 업무 관리 화면 연결 기능이 없습니다.',
}


class InstallationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(DIAGNOSTIC_MESSAGES[code])


def safe(path: Path):
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('연결된 경로는 관리 대상으로 사용하지 않습니다.')
    return path


def read_object(path: Path):
    safe(path)
    if not path.exists():
        return {}
    with path.open('rb') as stream:
        raw = stream.read(512 * 1024 + 1)
    if len(raw) > 512 * 1024:
        raise ValueError('설치 정보의 확인 범위를 초과했습니다.')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('설치 정보에 중복된 항목이 있습니다.')
            result[key] = value
        return result
    try:
        obj = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique)
    except RecursionError as exc:
        raise ValueError('설치 정보의 중첩 범위를 초과했습니다.') from exc
    if not isinstance(obj, dict):
        raise ValueError('설치 정보 형식을 확인해 주세요.')
    pending = [(obj, 0)]
    while pending:
        node, depth = pending.pop()
        if isinstance(node, (dict, list)):
            if depth > 32:
                raise ValueError('설치 정보의 중첩 범위를 초과했습니다.')
            pending.extend((item, depth + 1) for item in (node.values() if isinstance(node, dict) else node))
    return obj


class HarnessClient:
    def __init__(self, *, config=None, registrations=None):
        self.config_source = 'explicit' if config is not None else 'environment' if os.environ.get('CLAUDE_CONFIG_DIR') else 'default'
        self.config = Path(config or os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude').absolute()
        self.registrations = Path(registrations or Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'CompanyAgent/installations')

    def _installation(self, workspace):
        if __package__:
            from .skill_inventory import effective_plugins, same_profile_path
        else:
            from skill_inventory import effective_plugins, same_profile_path
        same_profile_path(self.config)
        project = safe(Path(workspace).absolute()) if workspace is not None else None
        files = [self.registrations / 'user/company-agent-install.json']
        projects = safe(self.registrations / 'projects')
        if project is not None and projects.is_dir():
            with os.scandir(projects) as stream:
                for i, entry in enumerate(stream):
                    if i >= 100:
                        raise ValueError('프로젝트 설치가 많아 범위를 확정하지 못했습니다. 설치 진단을 확인해 주세요.')
                    files.append(Path(entry.path) / 'company-agent-install.json')
        try:
            installed = read_object(self.config / 'plugins/installed_plugins.json').get('plugins', {})
            if not isinstance(installed, dict):
                raise ValueError('Invalid plugin registry')
        except (ValueError, OSError, UnicodeError) as exc:
            raise InstallationError('plugin_registry_invalid') from exc
        matches = []
        failures = []
        try:
            effective = effective_plugins(self.config, project)
        except (ValueError, OSError, UnicodeError, TypeError) as exc:
            raise InstallationError('plugin_registry_invalid') from exc
        for file in files:
            try:
                rec = read_object(file)
            except (ValueError, OSError, UnicodeError):
                failures.append('registration_invalid')
                continue
            if not rec:
                continue
            if rec.get('schemaVersion') != 1 or not isinstance(rec.get('claudeConfigRoot'), str):
                failures.append('registration_invalid')
                continue
            if rec.get('enabled') is False:
                failures.append('registration_disabled')
                continue
            if not Path(rec['claudeConfigRoot']).is_absolute() or Path(rec['claudeConfigRoot']).absolute() != self.config:
                failures.append('config_mismatch')
                continue
            if 'claudeConfigDirOverride' in rec and rec['claudeConfigDirOverride'] != bool(os.environ.get('CLAUDE_CONFIG_DIR')):
                failures.append('config_mismatch')
                continue
            scope, native = rec.get('scope'), rec.get('nativeClaudeScope')
            root_value = rec.get('projectRoot')
            root = Path(root_value) if isinstance(root_value, str) and root_value else None
            if scope == 'Project' and (project is None or root is None or not root.is_absolute() or not project.is_relative_to(root)):
                failures.append('scope_mismatch')
                continue
            if (scope == 'User' and native != 'user') or (scope == 'Project' and native not in {'local', 'project'}) or scope not in {'User', 'Project'}:
                failures.append('registration_invalid')
                continue
            plugin_id = rec.get('pluginId', 'company-agent@company-agent-local')
            settings = self.config / 'settings.json' if native == 'user' else root / '.claude' / ('settings.json' if native == 'project' else 'settings.local.json')
            try:
                enabled = read_object(settings).get('enabledPlugins', {})
                if not isinstance(enabled, dict):
                    raise ValueError('Invalid enabled plugins')
            except (ValueError, OSError, UnicodeError):
                failures.append('registration_invalid')
                continue
            if not isinstance(plugin_id, str) or enabled.get(plugin_id) is not True or effective.get(plugin_id) is not True:
                failures.append('plugin_disabled')
                continue
            records = installed.get(plugin_id, [])
            if not isinstance(records, list):
                failures.append('plugin_registry_invalid')
                continue
            entries = [x for x in records if isinstance(x, dict)
                       and x.get('scope') == native and x.get('version') == rec.get('coreVersion')
                       and (scope == 'User' or (isinstance(x.get('projectPath'), str) and Path(x['projectPath']).is_absolute() and Path(x['projectPath']).absolute() == root))]
            if len(entries) != 1:
                failures.append('registration_ambiguous' if len(entries) > 1 else 'version_mismatch')
                continue
            matches.append((len(root.parts) if scope == 'Project' else 0, rec, entries[0]))
        if not matches:
            raise InstallationError(failures[-1] if failures else 'registration_missing')
        matches.sort(key=lambda x: x[0], reverse=True)
        if len(matches) > 1 and matches[0][0] == matches[1][0]:
            raise InstallationError('registration_ambiguous')
        _, rec, entry = matches[0]
        try:
            plugin, python = same_profile_path(Path(entry.get('installPath', ''))), same_profile_path(Path(rec.get('pythonCommand', '')))
        except (ValueError, OSError, TypeError) as exc:
            raise InstallationError('runtime_unavailable') from exc
        if not plugin.is_absolute() or not python.is_absolute() or not python.is_file():
            raise InstallationError('runtime_unavailable')
        cli = safe(plugin / 'scripts/harness_cli.py')
        if not cli.is_file() or not (plugin / 'scripts/company_agent/workspace_api.py').is_file():
            raise InstallationError('api_unsupported')
        return project, rec, plugin, python

    def completion_inventory(self, workspace=None):
        """Native callable metadata only; no registered core or reference libraries."""
        from .skill_inventory import discover_native_metadata, same_profile_path
        same_profile_path(self.config)
        return discover_native_metadata(self.config, workspace, include_commands=True)

    def discovery_inventory(self, workspace=None):
        """Read metadata only, including when no Company Agent core is registered.

        This catalog path never imports an installed core, launches a registered
        Python, invokes a plugin, or reads skill instructions/settings secrets.
        """
        from .skill_inventory import discover_native_metadata, same_profile_path, _metadata_path
        same_profile_path(self.config)
        if workspace is not None:
            path = Path(workspace)
            if not path.is_absolute() or '..' in path.parts or not _metadata_path(path).is_dir():
                raise ValueError('스킬을 확인할 실제 업무 폴더를 선택해 주세요.')
            workspace = path
        references = []
        reference_failure = False
        def add_references(record, project):
            nonlocal reference_failure
            try:
                state = same_profile_path(Path(record['userStateRoot']))
                storage = 'project' if record.get('scope') == 'Project' else 'personal'
                references.append((state / 'personal-root/.claude/skills', 'personal', storage))
                if storage == 'personal' and project is not None:
                    key = str(project).casefold() if os.name == 'nt' else str(project)
                    project_state = state / 'project-scopes' / hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]
                    references.append((project_state / 'personal-root/.claude/skills', 'personal', 'project'))
                if record.get('knowledgeBaseRoot'):
                    knowledge = same_profile_path(Path(record['knowledgeBaseRoot']))
                    references.append((knowledge / '.claude/skills', 'corporate', 'company'))
            except (ValueError, OSError, TypeError, KeyError):
                reference_failure = True
        try:
            project, record, _, _ = self._installation(workspace)
            add_references(record, project)
            if record.get('scope') == 'Project':
                try:
                    _, user_record, _, _ = self._installation(None)
                    add_references(user_record, None)
                except (ValueError, OSError, TypeError, UnicodeError):
                    pass
            code = 'ready'
        except InstallationError as exc:
            code = exc.code
        except (ValueError, OSError, TypeError, UnicodeError):
            code = 'registration_invalid'
        snapshot = discover_native_metadata(self.config, workspace, reference_roots=references)
        if reference_failure:
            snapshot['skillsLimited'] = True
            snapshot['readFailures'] += 1
            snapshot['skillWarnings'].append('등록된 참고 스킬 저장소의 범위를 확인하지 못했습니다.')
        snapshot['context'] = {'configRootSource': self.config_source, 'actualCliContextVerified': False,
                               'scope': 'common' if workspace is None else 'folder'}
        snapshot['diagnostics'] = {
            'code': code, 'summary': DIAGNOSTIC_MESSAGES[code],
            'checks': [{'code': code, 'status': 'ok' if code == 'ready' else 'unavailable',
                        'message': DIAGNOSTIC_MESSAGES[code]},
                       {'code': 'metadata_partial' if snapshot['skillsLimited'] else 'metadata_read',
                        'status': 'partial' if snapshot['skillsLimited'] else 'ok',
                        'message': '일부 메타데이터를 읽지 못했습니다.' if snapshot['skillsLimited']
                                   else '선택 범위의 스킬 메타데이터를 읽었습니다.'},
                       {'code': 'cli_context_unverified', 'status': 'unknown',
                        'message': '앱이 확인한 설정 범위입니다. Claude 실행 래퍼가 변경한 설정 범위와의 일치는 아직 확인되지 않았습니다.'}]}
        return snapshot

    def choice_helper(self, workspace):
        """Internal origin evidence for a typed UI result; never execute it."""
        from .skill_inventory import same_profile_path
        _, record, plugin, python = self._installation(workspace)
        state = same_profile_path(Path(record['userStateRoot']))
        origin = {'python': str(python), 'script': str(plugin / 'scripts/harness_cli.py'),
                  'wrapper': str(plugin / 'scripts/Invoke-CompanyAgent.ps1'), 'stateRoot': str(state)}
        if os.name == 'nt':
            from .windows_process import powershell_path
            origin['powershell'] = powershell_path()
        return origin

    def locate(self, workspace):
        project, _, plugin, python = self._installation(workspace)
        return [str(python), '-X', 'utf8', '-B', str(plugin / 'scripts/harness_cli.py'),
                'workspace', '--project', str(project)]

    def skill_inventory(self, workspace):
        """Read installed skill metadata in its own validated interpreter.

        The helper supports existing cores without editing their installation
        or invoking the broader policy/context checks used by the settings UI.
        """
        project, _, plugin, python = self._installation(workspace)
        helper = Path(__file__).with_name('skill_inventory.py')
        command = [str(python), '-X', 'utf8', '-B', str(helper),
                   '--workspace', str(project), '--plugin', str(plugin),
                   '--config', str(self.config), '--registrations', str(self.registrations)]
        try:
            result = run_owned(command, capture_output=True, cwd=workspace,
                               timeout=20, creationflags=HIDDEN, output_limit=4 * 1024 * 1024)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('스킬 목록 확인 시간을 초과했습니다.') from exc
        except OutputLimitExceeded as exc:
            raise ValueError('설치된 스킬의 메타데이터를 확인하지 못했습니다.') from exc
        if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
            raise ValueError('설치된 스킬의 메타데이터를 확인하지 못했습니다.')
        value = json.loads(result.stdout.decode('utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError('스킬 목록 형식을 확인하지 못했습니다.')
        return value

    def call(self, workspace, request):
        command = self.locate(workspace)
        payload = json.dumps(request, ensure_ascii=False).encode('utf-8')
        if len(payload) > 64 * 1024:
            raise ValueError('변경 내용이 너무 큽니다.')
        try:
            result = run_owned(command, input=payload, capture_output=True, cwd=workspace,
                               timeout=20, creationflags=HIDDEN, output_limit=4 * 1024 * 1024)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('관리 응답 시간을 초과했습니다. 변경 요청이었다면 새로고침해 실제 저장 여부를 먼저 확인하세요. 자동 재실행하지 않습니다.') from exc
        except OutputLimitExceeded as exc:
            raise ValueError('관리 결과의 표시 범위를 초과했습니다.') from exc
        if len(result.stdout) > 4 * 1024 * 1024:
            raise ValueError('관리 결과의 표시 범위를 초과했습니다.')
        if result.returncode:
            try:
                message = json.loads(result.stderr.decode('utf-8-sig')).get('error')
            except (ValueError, UnicodeError):
                message = None
            raise ValueError(message or '업무 관리 연결을 확인하지 못했습니다. 기존 자료와 설정은 초기화하지 않습니다.')
        return json.loads(result.stdout.decode('utf-8-sig'))
