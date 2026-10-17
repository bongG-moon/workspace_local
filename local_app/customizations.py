"""Explicit Claude settings actions. Listing never starts a CLI or reads skill bodies."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import threading
import uuid

from .skill_inventory import (_json_metadata, _metadata_path, same_profile_path,
                              discover_native_metadata, effective_plugins, project_lineage)
from .owned_process import run_owned

MODES = {'on', 'off', 'name-only', 'user-invocable-only'}
SAVED = '설정을 저장했어요. 실행 중인 연결은 유지됩니다. 현재 연결에는 Claude Code 재시작 후 적용해 주세요.'


class Customizations:
    def __init__(self, config, command, info, *, runner=run_owned):
        self.config = Path(config).absolute()
        self.command, self.info, self.runner = command, info, runner
        self.lock = threading.Lock()
        self.cancel = threading.Event()

    def target(self, workspace, scope):
        same_profile_path(self.config)
        if scope == 'user':
            return _metadata_path(self.config / 'settings.json')
        if scope != 'local':
            raise ValueError('설정 범위는 이 프로젝트 또는 사용자 공통 중 선택해 주세요.')
        root = same_profile_path(Path(workspace))
        return _metadata_path(root / '.claude/settings.local.json')

    def policy(self):
        # Never override managed customization. CLI still enforces organization policy.
        paths = [self.config / 'managed-settings.json']
        if os.name == 'nt':
            paths.append(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'ClaudeCode/managed-settings.json')
        else:
            paths.extend([Path('/etc/claude-code/managed-settings.json'),
                          Path('/Library/Application Support/ClaudeCode/managed-settings.json')])
        policies = [_json_metadata(path) for path in paths]
        for path in paths[1:]:
            directory = _metadata_path(path.parent / 'managed-settings.d')
            if directory.is_dir():
                count = 0
                for entry in directory.iterdir():
                    if entry.name.startswith('.') or entry.suffix != '.json':
                        continue
                    count += 1
                    if count > 64:
                        raise ValueError('관리 정책 파일이 많아 원본 Claude에서 설정을 확인해야 해요.')
                    policies.append(_json_metadata(entry))
        if os.name == 'nt':
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, r'SOFTWARE\Policies\ClaudeCode', 0,
                                        winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                        raw, value_type = winreg.QueryValueEx(key, 'Settings')
                except FileNotFoundError:
                    continue
                if value_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or len(raw) > 512 * 1024:
                    raise ValueError('관리 정책 형식을 확인하지 못했어요.')
                policy = json.loads(raw)
                if not isinstance(policy, dict):
                    raise ValueError('관리 정책 형식을 확인하지 못했어요.')
                policies.append(policy)
        return policies

    @staticmethod
    def revision(path):
        _json_metadata(path)  # Bounded, strict, no symlinks or duplicate keys.
        raw = b''
        if path.exists():
            with path.open('rb') as stream:
                raw = stream.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024:
                raise ValueError('설정 파일 크기를 확인해 주세요.')
        return hashlib.sha256(raw).hexdigest()

    def snapshot(self, workspace, scope='local'):
        path = self.target(workspace, scope)
        policies = self.policy()
        inventory = discover_native_metadata(self.config, workspace, include_disabled=True)
        settings = _json_metadata(path)
        overrides = settings.get('skillOverrides', {})
        if not isinstance(overrides, dict):
            raise ValueError('기존 스킬 설정 형식을 확인해 주세요.')
        version = re.search(r'(\d+)\.(\d+)\.(\d+)', self.info.get('version', ''))
        supported = bool(version and tuple(map(int, version.groups())) >= (2, 1, 260))
        # Additional settings passed by a wrapper cannot safely be rewritten here.
        forced = any(arg == '--settings' or arg.startswith('--settings=') for arg in self.command)
        strict = any(p.get('strictPluginOnlyCustomization') for p in policies)
        lineage = project_lineage(workspace, self.config)
        project_plugins, project_skills = {}, {}
        for directory in reversed(lineage):
            for filename in ('settings.json', 'settings.local.json'):
                project_settings = _json_metadata(directory / '.claude' / filename)
                project_plugins.update(project_settings.get('enabledPlugins', {}))
                project_skills.update(project_settings.get('skillOverrides', {}))
        skills = []
        for row in inventory['skills']:
            if row.get('source') not in {'user', 'project'}:
                continue
            name = row['name']
            scope_conflict = scope == 'user' and name in project_skills
            locked = row.get('visibilityLocked') or forced or strict or not supported or scope_conflict or any(
                name in p.get('skillOverrides', {}) for p in policies)
            skills.append({key: row.get(key) for key in ('candidateId', 'name', 'description', 'source', 'visibility')} |
                          {'locked': bool(locked), 'override': overrides.get(name),
                           'reason': '이 프로젝트에 별도 설정이 있어요. 저장 범위를 이 프로젝트로 바꿔 주세요.' if scope_conflict
                           else 'CLI 버전·스킬 원본 또는 관리 정책에 의해 변경할 수 없어요.' if locked else ''})
        # A name is the native settings key. Do not offer two independent toggles for it.
        skills.sort(key=lambda row: row['source'] != 'user')
        skills = list({row['name']: row for row in reversed(skills)}.values())
        enabled = effective_plugins(self.config, workspace)
        registry = _json_metadata(self.config / 'plugins/installed_plugins.json').get('plugins', {})
        if not isinstance(registry, dict) or len(registry) > 1000:
            raise ValueError('플러그인 설치 목록을 확인하지 못했어요.')
        plugins = []
        for name, entries in registry.items():
            if not isinstance(name, str) or not re.fullmatch(r'[\w.-]+@[\w.-]+', name) or not isinstance(entries, list):
                continue
            eligible = any(isinstance(item, dict) and (item.get('scope') == 'user' or
                           item.get('scope') in {'local', 'project'} and Path(item.get('projectPath', '')) in lineage)
                           for item in entries[:1000])
            if not eligible:
                continue
            scope_conflict = scope == 'user' and name in project_plugins
            locked = forced or scope_conflict or any(name in p.get('enabledPlugins', {}) for p in policies)
            plugins.append({'name': name, 'enabled': enabled.get(name) is True, 'locked': locked,
                            'override': settings.get('enabledPlugins', {}).get(name),
                            'reason': '이 프로젝트에 별도 설정이 있어요. 저장 범위를 이 프로젝트로 바꿔 주세요.' if scope_conflict
                            else '관리 정책에서 지정한 플러그인이에요.' if locked else ''})
        return {'skills': skills, 'plugins': plugins, 'scope': scope, 'settingsPath': str(path),
                'revision': self.revision(path), 'limited': inventory.get('skillsLimited', False),
                'notice': '설정 변경은 같은 Claude 설정을 사용하는 터미널에도 반영돼요. 실행 중인 연결은 재시작 후 적용됩니다.'}

    def change(self, workspace, request):
        if not self.lock.acquire(blocking=False):
            raise ValueError('이전 설정 변경이 끝난 뒤 다시 시도해 주세요.')
        try:
            scope = request.get('scope', 'local')
            snapshot = self.snapshot(workspace, scope)
            if request.get('revision') != snapshot['revision']:
                raise ValueError('다른 곳에서 설정이 변경됐어요. 목록을 다시 확인해 주세요.')
            kind, name, value = request.get('kind'), request.get('name'), request.get('value')
            if kind not in {'skills', 'plugins'}:
                raise ValueError('변경할 설정 종류를 확인해 주세요.')
            row = next((row for row in snapshot[kind] if row['name'] == name), None)
            if row is None or row['locked']:
                raise ValueError('현재 목록에서 변경할 수 있는 항목을 선택해 주세요.')
            path = self.target(workspace, scope)
            if kind == 'skills':
                if value is not None and (not isinstance(value, str) or value not in MODES):
                    raise ValueError('지원하는 스킬 사용 상태를 선택해 주세요.')
                settings = _json_metadata(path)
                overrides = settings.setdefault('skillOverrides', {})
                if value is None:
                    overrides.pop(name, None)
                else:
                    overrides[name] = value
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name('.workspace-settings-' + uuid.uuid4().hex + '.tmp')
                try:
                    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
                        json.dump(settings, stream, ensure_ascii=False, indent=2)
                        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
                    if self.revision(path) != snapshot['revision']:
                        raise ValueError('저장 중 설정이 변경됐어요. 목록을 새로고침해 주세요.')
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            else:
                if type(value) is not bool:
                    raise ValueError('플러그인의 사용 여부를 선택해 주세요.')
                action = 'enable' if value else 'disable'
                result = self.runner(self.command + ['plugin', action, name, '--scope', scope],
                                     cwd=workspace, timeout=30, capture_output=True,
                                     cancel_event=self.cancel,
                                     encoding='utf-8', errors='replace', output_limit=256 * 1024,
                                     creationflags=0x08000000 if os.name == 'nt' else 0)
                if result.returncode:
                    raise ValueError('Claude가 변경을 완료하지 못했어요. 플러그인 의존성·회사 정책·CLI의 --scope 지원을 확인해 주세요. 원본 터미널에서도 확인할 수 있어요.')
                if _json_metadata(path).get('enabledPlugins', {}).get(name) is not value:
                    raise ValueError('CLI 명령은 끝났지만 선택한 프로필의 적용 상태를 확인하지 못했어요. 실행 래퍼의 Claude 설정 경로를 확인하고 목록을 새로고침해 주세요.')
            catalog = self.snapshot(workspace, scope)
            confirmed = next((row for row in catalog[kind] if row['name'] == name), None)
            # A higher-priority file can change while the local write / CLI runs.
            # Do not report success when the selected setting is now overridden.
            if (confirmed is None or confirmed['locked'] or confirmed['override'] != value
                    or value is not None and confirmed['visibility' if kind == 'skills' else 'enabled'] != value):
                raise ValueError('설정 파일은 저장됐지만 현재 프로젝트의 적용 상태가 달라졌어요. 저장 범위와 프로젝트·관리 설정을 다시 확인해 주세요.')
            return {'ok': True, 'reconnectRequired': True, 'notice': SAVED, 'catalog': catalog}
        finally:
            self.lock.release()
