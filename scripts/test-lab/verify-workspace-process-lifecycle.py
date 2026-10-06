"""Windows wrapper lifecycle stress test using an isolated synthetic CLI only.

No installed Claude command, model request, user profile, personal state or
process-name cleanup is used. Retained Windows jobs report exact owned counts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from local_app.bridge import probe_cli, terminal_command
from local_app.server import LocalApp


def eventually(predicate, description, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError(description)


def verify(output, cycles):
    if os.name != 'nt':
        raise RuntimeError('This QA requires real Windows PowerShell and Windows jobs.')
    output.mkdir(parents=True, exist_ok=True)
    case = output / ('run-' + uuid.uuid4().hex[:12])
    case.mkdir()
    config = case / 'isolated-claude-config'
    config.mkdir()
    fixture = case / 'permission-cli.py'
    source = (ROOT / 'tests/fixtures/workspace_permission_cli.py').read_text(encoding='utf-8')
    # Add a same-session interrupt terminal response and honor --resume only
    # in this isolated copy. The base fixture otherwise intentionally hangs.
    old_session = 'session = str(uuid.uuid4())'
    new_session = ("session = next((arg.split('=', 1)[1] for arg in sys.argv "
                   "if arg.startswith('--resume=')), str(uuid.uuid4()))")
    old_ack = "        else:\n            emit(frame)\n    elif value['type'] == 'user':"
    new_ack = ("        else:\n            emit(frame)\n"
               "        if request['subtype'] == 'interrupt':\n"
               "            result('Fixture interrupted')\n"
               "    elif value['type'] == 'user':")
    assert source.count(old_session) == source.count(old_ack) == 1
    source = source.replace("if '--version' in sys.argv:", "HELP += '\\n--resume <session-id>\\n'\nif '--version' in sys.argv:", 1)
    fixture.write_text(source.replace(old_session, new_session).replace(old_ack, new_ack), encoding='utf-8')
    report = {'createdUtc': datetime.now(timezone.utc).isoformat(), 'status': 'running',
              'scope': 'synthetic_cli_through_real_powershell', 'cycles': [],
              'requestedCycles': cycles, 'realClaudeRequests': 0, 'personalProfilesLoaded': False,
              'fixtureSha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
              'evidenceDirectory': str(case), 'measurements': 'retained Windows job active process counts'}
    app = None
    jobs = []
    try:
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(config),
                                     'WORKSPACE_PERMISSION_FIXTURE': 'runtime-live'}):
            command = terminal_command(sys.executable) + ['-B', str(fixture)]
            app = LocalApp(case / 'state', command=command, info=probe_cli(command),
                           managed_workspace_root=case / 'managed')
            sid = app.create(str(case), True)['id']
            item = app.get(sid)
            previous_session = None
            for index in range(cycles):
                started = time.monotonic()
                app.connect(sid)
                bridge = item['bridge']
                job = bridge._process_job
                assert job is not None and job.assigned
                jobs.append(job)
                parent = bridge.process
                row = {'cycle': index + 1, 'wrapperPid': parent.pid,
                       'ownedAfterConnect': job.active_processes()}
                assert row['ownedAfterConnect'] >= 2, 'PowerShell and fixture must both be alive'
                app.send(sid, 'hold', [])
                eventually(lambda: item.get('sessionId') and bridge.busy, 'Fixture run did not start')
                session = item['sessionId']
                if previous_session is not None:
                    assert previous_session == session
                previous_session = session
                run_id = item['lastRunId']
                selected = 'plan' if index % 2 == 0 else 'auto'
                result = app.set_permission_mode(sid, selected)
                assert result['permissionMode'] == selected
                assert item['state'] == 'running' and bridge.busy
                assert (bridge.process.pid, item['sessionId'], item['lastRunId']) == (parent.pid, session, run_id), 'Mode change replaced the running fixture identity'
                row['liveModeApplied'] = selected
                row['samePidDuringModeChange'] = True
                row['ownedDuringWork'] = job.active_processes()
                app.stop(sid)
                eventually(lambda: app.stop_state(item) == 'stopped', 'Soft stop did not settle')
                assert parent.poll() is None and not bridge.closed
                row['ownedAfterSoftStop'] = job.active_processes()
                row['samePidAfterSoftStop'] = item['bridge'].process.pid == parent.pid
                app.send(sid, 'fixture follow-up', [])
                eventually(lambda: item['state'] == 'done', 'Follow-up did not finish')
                assert item['bridge'].process.pid == parent.pid
                row['samePidAfterFollowup'] = True
                app.stop(sid, disconnect=True)
                eventually(lambda: bridge.cleanup_complete, 'Disconnect did not clean exact owned job')
                row['ownedAfterDisconnect'] = job.active_processes()
                row['allPreviouslyOwnedAfterDisconnect'] = sum(old.active_processes() for old in jobs)
                assert row['ownedAfterDisconnect'] == row['allPreviouslyOwnedAfterDisconnect'] == 0
                row['elapsedSeconds'] = round(time.monotonic() - started, 3)
                report['cycles'].append(row)
            # Also close the whole app while a final request is running.
            app.connect(sid)
            bridge = item['bridge']
            jobs.append(bridge._process_job)
            app.send(sid, 'hold', [])
            eventually(lambda: bridge.busy, 'Final hold did not start')
            report['ownedBeforeAppClose'] = sum(job.active_processes() for job in jobs)
            report['appClosed'] = app.close(force=True)
            report['ownedAfterAppClose'] = sum(job.active_processes() for job in jobs)
            assert report['appClosed'] and report['ownedAfterAppClose'] == 0
            report['maximumOwnedDuringOneCycle'] = max(row['ownedDuringWork'] for row in report['cycles'])
            report['status'] = 'passed'
    except Exception as exc:
        report['status'] = 'failed'
        report['errorType'] = type(exc).__name__
        report['error'] = str(exc)
        raise
    finally:
        if app is not None:
            report['finalCleanupConfirmed'] = app.close(force=True)
        report['retainedJobCount'] = len(jobs)
        report['finalOwnedProcessCount'] = sum(job.active_processes() for job in jobs)
        (case / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        (output / 'latest-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'status': report['status'], 'cyclesCompleted': len(report['cycles']),
                          'finalOwnedProcessCount': report['finalOwnedProcessCount'],
                          'evidence': str(case / 'result.json')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/qa-process-lifecycle-0.23.20')
    parser.add_argument('--cycles', type=int, choices=range(1, 17), default=8)
    args = parser.parse_args()
    verify(args.output.resolve(), args.cycles)
