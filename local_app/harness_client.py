"""Locate the active installer registration, then call its typed local service.

Never import another installation into this process, invent a state directory,
or launch model work. The core re-resolves the registration on every operation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def safe(path: Path):
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
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
    obj = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(obj, dict):
        raise ValueError('설치 정보 형식을 확인해 주세요.')
    return obj


class HarnessClient:
    def __init__(self, *, config=None, registrations=None):
        self.config = Path(config or os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude').absolute()
        self.registrations = Path(registrations or Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'CompanyAgent/installations')

    def _installation(self, workspace):
        project = safe(Path(workspace).absolute())
        files = [self.registrations / 'user/company-agent-install.json']
        projects = safe(self.registrations / 'projects')
        if projects.is_dir():
            with os.scandir(projects) as stream:
                for i, entry in enumerate(stream):
                    if i >= 100:
                        raise ValueError('프로젝트 설치가 많아 범위를 확정하지 못했습니다. 설치 진단을 확인해 주세요.')
                    files.append(Path(entry.path) / 'company-agent-install.json')
        installed = read_object(self.config / 'plugins/installed_plugins.json').get('plugins', {})
        matches = []
        for file in files:
            rec = read_object(file)
            if rec.get('schemaVersion') != 1 or rec.get('enabled') is False or not rec.get('claudeConfigRoot'):
                continue
            if Path(rec['claudeConfigRoot']).absolute() != self.config:
                continue
            if 'claudeConfigDirOverride' in rec and rec['claudeConfigDirOverride'] != bool(os.environ.get('CLAUDE_CONFIG_DIR')):
                continue
            scope, native = rec.get('scope'), rec.get('nativeClaudeScope')
            root = Path(rec['projectRoot']).absolute() if rec.get('projectRoot') else None
            if scope == 'Project' and (root is None or not project.is_relative_to(root)):
                continue
            if scope not in {'User', 'Project'} or native not in {'user', 'local', 'project'}:
                continue
            plugin_id = rec.get('pluginId', 'company-agent@company-agent-local')
            settings = self.config / 'settings.json' if native == 'user' else root / '.claude/settings.local.json'
            if read_object(settings).get('enabledPlugins', {}).get(plugin_id) is not True:
                continue
            entries = [x for x in installed.get(plugin_id, []) if isinstance(x, dict)
                       and x.get('scope') == native and x.get('version') == rec.get('coreVersion')
                       and (scope == 'User' or (x.get('projectPath') and Path(x['projectPath']).absolute() == root))]
            if len(entries) != 1:
                continue
            matches.append((len(root.parts) if scope == 'Project' else 0, rec, entries[0]))
        if not matches:
            raise ValueError('현재 폴더의 활성 Company Agent 설치를 확인하지 못했습니다. 일반 Claude 대화는 그대로 사용할 수 있습니다.')
        matches.sort(key=lambda x: x[0], reverse=True)
        if len(matches) > 1 and matches[0][0] == matches[1][0]:
            raise ValueError('같은 범위의 설치 정보가 겹칩니다. 임의로 선택하지 않았습니다.')
        _, rec, entry = matches[0]
        plugin, python = safe(Path(entry.get('installPath', ''))), safe(Path(rec.get('pythonCommand', '')))
        if not plugin.is_absolute() or not python.is_absolute() or not python.is_file():
            raise ValueError('설치에 등록된 실행 경로를 확인해 주세요.')
        cli = safe(plugin / 'scripts/harness_cli.py')
        if not cli.is_file() or not (plugin / 'scripts/company_agent/workspace_api.py').is_file():
            raise ValueError('이 설치에는 업무 관리 화면 연결 기능이 없습니다. 새 Company Agent 설치본이 필요합니다. 일반 대화는 계속 사용할 수 있습니다.')
        return project, rec, plugin, python

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
            result = subprocess.run(command, capture_output=True, cwd=workspace,
                                    timeout=20, creationflags=HIDDEN)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('스킬 목록 확인 시간을 초과했습니다.') from exc
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
            result = subprocess.run(command, input=payload, capture_output=True, cwd=workspace,
                                    timeout=20, creationflags=HIDDEN)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('관리 응답 시간을 초과했습니다. 변경 요청이었다면 새로고침해 실제 저장 여부를 먼저 확인하세요. 자동 재실행하지 않습니다.') from exc
        if len(result.stdout) > 4 * 1024 * 1024:
            raise ValueError('관리 결과의 표시 범위를 초과했습니다.')
        if result.returncode:
            try:
                message = json.loads(result.stderr.decode('utf-8-sig')).get('error')
            except (ValueError, UnicodeError):
                message = None
            raise ValueError(message or '업무 관리 연결을 확인하지 못했습니다. 기존 자료와 설정은 초기화하지 않습니다.')
        return json.loads(result.stdout.decode('utf-8-sig'))
