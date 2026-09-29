"""Stop-hook transport status is evidence of a check, not artifact success.

The lifecycle fields follow the published Anthropic SDKHookResponseMessage.
Raw stderr/stdout are never forwarded to the UI as they may contain secrets.
"""
from __future__ import annotations

import json


def project(data):
    if data.get('hook_event') != 'Stop' or data.get('parent_tool_use_id') or data.get('agent_id'):
        return None
    subtype = data.get('subtype')
    if subtype == 'hook_started':
        return {'state': 'checking', 'message': '종료 전에 결과 확인 절차를 진행하고 있어요.'}
    if subtype != 'hook_response':
        return None
    output = None
    raw = data.get('stdout')
    if isinstance(raw, str) and len(raw) <= 32000:
        try:
            output = json.loads(raw)
        except (ValueError, RecursionError):
            pass
    # Stop's JSON block is a normal correction request, not a hook crash.
    if isinstance(output, dict) and output.get('decision') == 'block':
        return {'state': 'checking', 'message': '결과 확인 절차가 추가 확인을 요청했어요. 진행 결과를 기다리고 있습니다.'}
    outcome = data.get('outcome')
    if outcome == 'error':
        return {'state': 'needs-review', 'message': '종료 확인 절차에서 오류가 보고됐어요. 결과 확인을 마쳤는지 점검해 주세요.'}
    if outcome == 'cancelled':
        return {'state': 'unverified', 'message': '종료 확인 절차가 취소됐어요. 결과 검증 완료는 확인하지 못했습니다.'}
    if outcome == 'success':
        return {'state': 'unverified', 'message': '종료 확인 절차는 끝났어요. 이 신호만으로 결과물 검증 성공을 확정하지 않습니다.'}
    return {'state': 'unverified', 'message': '종료 확인 절차의 결과를 확인하지 못했습니다.'}


def at_result(current):
    if isinstance(current, dict) and current.get('state') == 'needs-review':
        return dict(current)
    return {'state': 'unverified', 'message': '요청 처리가 끝났어요. 결과물의 최종 확인은 별도로 필요합니다.'}
