"""Local-only guidance, user assessments and observed per-request telemetry.

Opening this panel does not run Claude. Model usage snapshots are never added
to transcript totals. Only explicit learning/progress/outcome actions persist.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import secrets
import threading
import time

from .harness_client import HarnessClient, safe, read_object

METRICS = ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')
STEPS = {'read', 'report', 'revise', 'remember', 'reuse'}
STATES = {'passed', 'failed', 'unverified', 'cancelled', 'blocked'}


def course():
    path = Path(__file__).resolve().parents[1] / 'company-agent-plugin/resources/onboarding-course.json'
    return read_object(path)


def count(value):
    return value if type(value) is int and 0 <= value <= 10**12 else None


def begin_turn(item):
    item['observation'] = {'id': secrets.token_hex(16), 'started': time.monotonic(), 'phaseAt': time.monotonic(),
        'phase': 'starting', 'intervals': {}, 'tools': {}, 'toolIds': set(), 'skills': set(), 'terminal': None,
        'usage': None, 'previewed': set(), 'fileOpened': False, 'tokenAlert': item.get('tokenAlert')}


def observe(item, kind, data):
    obs = item.get('observation')
    if not obs:
        return
    if kind == 'activity' and obs['terminal'] is None:
        callid = data.get('id')
        if callid and callid in obs['toolIds']:
            return
        if callid:
            obs['toolIds'].add(callid)
        tool = data.get('tool') or 'Other'
        obs['tools'][tool] = obs['tools'].get(tool, 0) + 1
        if tool == 'Skill' and data.get('skill'):
            obs['skills'].add(str(data['skill'])[:160])
    terminal = kind in {'result', 'error'} or (kind == 'status' and data.get('state') == 'stopped')
    if obs['terminal'] is not None:
        return  # repeated result delivery cannot add usage/duration twice
    now = time.monotonic()
    elapsed = round((now - obs['phaseAt']) * 1000)
    obs['intervals'][obs['phase']] = obs['intervals'].get(obs['phase'], 0) + elapsed
    obs['phaseAt'] = now
    obs['phase'] = item.get('state', 'running')
    if terminal:
        obs['terminal'] = item['state']
        obs['wallMs'] = round((now - obs['started']) * 1000)
        if kind == 'result':
            usage = data.get('usage') if isinstance(data.get('usage'), dict) else {}
            obs['usage'] = {key: count(usage.get(key)) for key in METRICS}
            obs['cliDurationMs'] = count(data.get('durationMs'))
            cost = data.get('costUsd')
            obs['costUsd'] = cost if type(cost) in {int, float} and math.isfinite(cost) and 0 <= cost < 10**9 else None
            observed = [value for value in obs['usage'].values() if value is not None]
            if obs.get('tokenAlert') and sum(observed) >= obs['tokenAlert']:
                obs['budgetWarning'] = 'CLI가 보고한 토큰이 알림 기준을 넘었습니다. 누적값일 수 있으며 강제 예산 중단이나 추가 청구액을 뜻하지 않습니다.'
                data['budgetWarning'] = obs['budgetWarning']


def telemetry(item):
    obs = item.get('observation')
    if not obs:
        return {'status': 'unavailable', 'notice': '이 앱 실행 중 관찰한 요청이 없습니다. 과거 사용량은 자동 검색하지 않습니다.'}
    intervals = dict(obs['intervals'])
    if obs['terminal'] is None:
        intervals[obs['phase']] = intervals.get(obs['phase'], 0) + round((time.monotonic() - obs['phaseAt']) * 1000)
    return {'status': 'observed' if obs['terminal'] else 'in-progress', 'requestId': obs['id'],
        'tokens': obs['usage'] or {key: None for key in METRICS}, 'cliReportedCostUsd': obs.get('costUsd'),
        'cliDurationMs': obs.get('cliDurationMs'), 'wallMs': obs.get('wallMs'), 'phaseMs': intervals,
        'tools': obs['tools'], 'skills': sorted(obs['skills']), 'artifactPreviewObserved': bool(obs['previewed']),
        'nativeOpenRequested': obs['fileOpened'],
        'tokenAlert': obs.get('tokenAlert'), 'budgetWarning': obs.get('budgetWarning'),
        'notice': '현재 요청의 CLI 결과 스냅샷입니다. CLI 버전에 따라 세션 누적일 수 있어 요청끼리 합산하지 않습니다. 로그 분석과도 더하지 않습니다. 토큰 누락은 0이 아니며 실행 시간에는 후크·도구 시간이 포함됩니다. 파일 열기 요청은 내용 검증이 아닙니다.'}


class Companion:
    def __init__(self, state, *, client=None, demo=False):
        self.state, self.client, self.demo = Path(state), client or HarnessClient(), demo
        self.lock = threading.RLock()
        self.pending = {}

    def _file(self, workspace):
        # Folder-scoped self-checks, NOT a third memory/policy owner.
        key = hashlib.sha256(str(Path(workspace).absolute()).casefold().encode()).hexdigest()
        return safe(self.state / 'learning-progress' / (key + '.json'))

    def records(self, workspace):
        return read_object(self._file(workspace)) or {'schemaVersion': 1, 'steps': {}, 'outcomes': []}

    def save(self, workspace, data):
        path = self._file(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = safe(path.with_suffix('.tmp'))
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        tmp.replace(path)

    def snapshot(self, item, view='checks'):
        if view not in {'guide','memory','knowledge','brief','usage','checks','map',
                        'shared-memory','my-memory','shared-harness','my-harness',
                        'personal-memory','project-memory','personal-harness','project-harness'}:
            raise ValueError('지원하지 않는 관리 화면입니다.')
        result = {'course': course(), 'records': self.records(item['workspace']), 'telemetry': telemetry(item), 'demo': self.demo}
        if self.demo:
            result['unavailable'] = '화면 체험 모드입니다. 실제 기억·설정·로그는 읽거나 변경하지 않습니다.'
        elif view not in {'guide', 'usage'}:
            try:
                request = {'operation': 'snapshot', 'view':view}
                if view == 'map' and item.get('sessionId'):
                    request['sessionId'] = item['sessionId']
                result['harness'] = self.client.call(item['workspace'], request)
                item['reviewScope'] = {key: result['harness']['scope'].get(key) for key in ('id', 'kind', 'coreVersion')}
            except (ValueError, OSError) as exc:
                result['unavailable'] = ('현재 Company Agent 설치가 새 관리 화면을 지원하지 않습니다. '
                    'Workspace와 Company Agent를 같은 배포본으로 업데이트해 주세요. 기존 자료와 대화는 그대로입니다.'
                    if '지원하지 않는 관리 화면' in str(exc) else str(exc))
        outcomes = result['records'].get('outcomes', [])
        groups = {}
        for row in outcomes:
            key = (row['workflow'], row.get('model'), (row.get('reviewScope') or {}).get('coreVersion'), row.get('demo'))
            group = groups.setdefault(key, {'workflow': key[0], 'model': key[1], 'coreVersionAtReview': key[2], 'demo': key[3], 'total': 0, 'counts': {}, 'repairsReported': 0, 'reportedRepairs': 0})
            group['total'] += 1
            group['counts'][row['status']] = group['counts'].get(row['status'], 0) + 1
            if type(row.get('userReportedRepairs')) is int:
                group['repairsReported'] += 1
                group['reportedRepairs'] += row['userReportedRepairs']
        result['assessment'] = {'total': len(outcomes), 'counts': dict(Counter(x['status'] for x in outcomes)), 'groups': list(groups.values()),
            'notice': '사용자가 직접 확인해 저장한 기록만 집계합니다. 미평가 업무는 포함하지 않으며, 전체 모델 성능 점수가 아닙니다.'}
        return result

    def action(self, item, request):
        with self.lock:
            action = request.get('action')
            workspace = item['workspace']
            if action == 'budget':
                value = request.get('tokenAlert')
                if value is not None and (type(value) is not int or not 1 <= value <= 10**9):
                    raise ValueError('알림 기준은 1~1,000,000,000 토큰 또는 해제로 입력하세요.')
                item['tokenAlert'] = value
                return {'ok': True, 'notice': '이 대화의 다음 요청부터 알립니다. 앱을 닫으면 해제됩니다. 실시간 강제 한도나 비용 한도가 아닙니다.'}
            if action == 'records-export':
                if request.get('confirmed') is not True:
                    raise ValueError('확인 기록 내보내기를 선택해 주세요.')
                records = self.records(workspace)
                # No prompts, paths, native session identifiers, skill names or model aliases.
                return {'schemaVersion': 1, 'source': 'user-self-check', 'steps': records['steps'],
                    'outcomes': [{k: row.get(k) for k in ('workflow', 'status', 'checks', 'userReportedRepairs', 'demo', 'observedState')} for row in records['outcomes']]}
            if action == 'records-clear':
                if request.get('confirmed') is not True:
                    raise ValueError('확인 기록 초기화를 선택해 주세요.')
                old = self.records(workspace)
                self.save(workspace, {'schemaVersion':1, 'steps':{}, 'outcomes':[]})
                return {'ok':True, 'notice':'이 폴더의 따라 하기·자기 확인 기록만 비웠습니다. 대화·기억·결과 파일은 그대로입니다.', 'removedRecords':len(old['outcomes'])}
            if action == 'progress':
                if request.get('step') not in STEPS or type(request.get('checked')) is not bool:
                    raise ValueError('올바른 단계와 확인 여부를 선택해 주세요.')
                records = self.records(workspace)
                records['steps'][request['step']] = {'checked': request['checked'], 'userConfirmedAt': time.time(), 'courseVersion': course()['version']}
                self.save(workspace, records)
                return {'ok': True, 'notice': '직접 확인한 학습 진행만 저장했습니다. 업무 성공을 자동 판정하지 않습니다.'}
            if action == 'outcome':
                if request.get('confirmed') is not True or request.get('status') not in STATES or request.get('workflow') not in STEPS:
                    raise ValueError('업무 종류와 확인 결과를 선택해 주세요.')
                obs = item.get('observation')
                if not obs or obs['terminal'] is None or request.get('requestId') != obs['id']:
                    raise ValueError('종료된 현재 요청을 먼저 확인해 주세요.')
                checks = request.get('checks', [])
                if not isinstance(checks, list) or any(x not in {'content', 'scope', 'artifact', 'noOverwrite'} for x in checks):
                    raise ValueError('지원하는 확인 항목만 선택해 주세요.')
                if request['status'] == 'passed' and (obs['terminal'] != 'done' or not {'content', 'scope'}.issubset(checks)):
                    raise ValueError('완료된 요청의 내용과 확인 범위를 직접 대조해야 통과로 기록합니다.')
                repairs = request.get('repairs')
                if repairs is not None and (type(repairs) is not int or not 0 <= repairs <= 100):
                    raise ValueError('수정 횟수는 0~100 또는 미확인으로 입력해 주세요.')
                connection = item.get('connection') or {}
                record = {'id': obs['id'], 'workflow': request['workflow'], 'status': request['status'],
                    'checks': sorted(set(checks)), 'userReportedRepairs': repairs, 'time': time.time(),
                    'model': str(connection.get('model') or 'unavailable')[:160], 'demo': self.demo,
                    'source': 'user-confirmed', 'artifactPreviewObserved': bool(obs['previewed']),
                    'reviewScope': item.get('reviewScope'), 'uiVersion': '0.8',
                    'observedState': obs['terminal'], 'skillsObserved': sorted(obs['skills'])}
                records = self.records(workspace)
                records['outcomes'] = [x for x in records['outcomes'] if x['id'] != record['id']][-99:] + [record]
                self.save(workspace, records)
                return {'ok': True, 'notice': '자기 확인 기록을 저장했습니다. AI가 보장한 성능 평가가 아닙니다.'}
            if self.demo:
                raise ValueError('체험 모드에서는 실제 기억·설정·로그를 변경하거나 읽지 않습니다.')
            if action == 'plan':
                plan = self.client.call(workspace, {'operation': 'plan', 'data': request.get('data', {})})
                now = time.monotonic()
                self.pending = {key: value for key, value in self.pending.items() if now - value['at'] < 600}
                if len(self.pending) >= 30:
                    raise ValueError('확인 중인 변경안이 많습니다. 잠시 후 다시 열어 주세요.')
                token = secrets.token_urlsafe(24)
                self.pending[token] = {'at': now, 'workspace': workspace, 'session': item['id'], 'plan': plan}
                return {'token': token, 'preview': plan}
            if action == 'apply':
                pending = self.pending.get(request.get('token'))
                if request.get('confirmed') is not True or not pending or pending['session'] != item['id'] or pending['workspace'] != workspace or time.monotonic() - pending['at'] > 600:
                    raise ValueError('변경안이 없거나 만료되었습니다. 다시 미리보기를 확인해 주세요.')
                if 'result' not in pending:
                    pending['result'] = self.client.call(workspace, {'operation': 'apply', 'plan': pending['plan'], 'confirmed': True})
                return pending['result']
            if action in {'versions', 'learning', 'rollback', 'share', 'usage', 'list', 'detail'}:
                # Exact typed fields only; callers cannot inject a command or root.
                allowed = {'versions': ('itemId',), 'learning': ('enabled', 'confirmed'),
                    'rollback': ('changeId', 'confirmed'), 'share': ('itemIds', 'confirmed'),
                    'usage': ('paths',), 'list':('category','cursor'),
                    'detail':('category','entryKey')}[action]
                return self.client.call(workspace, {'operation': action,
                    **({'storageScope':request['storageScope']} if 'storageScope' in request else {}),
                    **{key: request[key] for key in allowed if key in request}})
            raise ValueError('지원하지 않는 안내 작업입니다.')
