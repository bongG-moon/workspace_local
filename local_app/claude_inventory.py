"""Read Claude's native skill and plugin metadata without invoking a harness."""
from __future__ import annotations

import os
from pathlib import Path

from .skill_inventory import discover_native_metadata, same_profile_path


class ClaudeInventory:
    def __init__(self, *, config=None):
        self.config_source = ('explicit' if config is not None else
                              'environment' if os.environ.get('CLAUDE_CONFIG_DIR') else 'default')
        self.config = Path(config or os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude').absolute()

    def completion_inventory(self, workspace=None):
        same_profile_path(self.config)
        return discover_native_metadata(self.config, workspace, include_commands=True)

    def discovery_inventory(self, workspace=None):
        same_profile_path(self.config)
        snapshot = discover_native_metadata(self.config, workspace)
        limited = snapshot.get('skillsLimited') is True
        code = 'metadata_partial' if limited else 'metadata_read'
        message = '일부 스킬 정보를 읽지 못했습니다.' if limited else 'Claude 설정 범위의 스킬 정보를 확인했습니다.'
        snapshot['context'] = {'configRootSource': self.config_source,
                               'actualCliContextVerified': False,
                               'scope': 'common' if workspace is None else 'folder'}
        snapshot['diagnostics'] = {'code': code, 'summary': message,
            'checks': [{'code': code, 'status': 'partial' if limited else 'ok', 'message': message},
                       {'code': 'cli_context_unverified', 'status': 'unknown',
                        'message': '설치 정보와 현재 CLI가 보고한 목록을 구분합니다. 실행 래퍼가 변경한 설정 위치는 확인되지 않을 수 있습니다.'}]}
        return snapshot
