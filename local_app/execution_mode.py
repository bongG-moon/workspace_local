"""Execution policy describes the inherited Windows process token.

No preference file, UAC launcher, background controller or privilege transition
is owned by the app. Windows chooses the token when its executable is opened.
"""
from __future__ import annotations

MODES = {'normal', 'administrator'}
# Same-version updates replace builds with the removed settings-switch policy.
EXECUTION_MODE_PROTOCOL = 3


def checked_mode(value):
    if not isinstance(value, str) or value not in MODES:
        raise ValueError('실행 권한을 확인하지 못했습니다.')
    return value


def checked_request(value):
    if value == 'auto':
        return value
    return checked_mode(value)
