"""Opt-in shipped-browser archive/exit checks and bounded idle/load measurements.

Uses temporary demo LocalApp servers, test-owned browsers and synthetic text.
No installed app, personal profile, corporate endpoint or real model is used.
psutil is optional test instrumentation, never an application dependency.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def worker(source, state):
    sys.path.insert(0, str(source))
    os.environ['LOCALAPPDATA'] = str(state / 'local')
    os.environ['CLAUDE_CONFIG_DIR'] = str(state / 'claude')
    from local_app.server import LocalApp, Server, WORKSPACE_VERSION
    import local_app.demo

    def synthetic_reply(app, sid, text):
        app.emit(sid, 'assistant', {'text': '검증용 응답: ' + text})
        app.emit(sid, 'result', {'durationMs': 1, 'usage': {}})

    local_app.demo.run = synthetic_reply
    app = LocalApp(state / 'state', demo=True)
    workspace = state / 'workspace'
    workspace.mkdir(parents=True)
    old = app.create(str(workspace), True, title='보관 검증 업무')['id']
    work = app.create(str(workspace), True, title='현재 검증 업무')['id']
    chat = app.create(kind='chat', title='일반 대화 검증')['id']
    app.save_draft({'id': old, 'selectedId': old, 'revision': 0,
        'draft': {'text': '이미 저장된 초안', 'attachments': []}, 'stash': None})
    server = Server(app)
    print(json.dumps({'origin': server.origin, 'token': app.token, 'archived': old,
        'work': work, 'chat': chat, 'version': WORKSPACE_VERSION}), flush=True)
    try:
        server.serve_forever(poll_interval=.05)
    finally:
        app.close()
        snapshot = app.drafts.snapshot()
        (state / 'final-drafts.json').write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--cycles', type=int, default=50)
    parser.add_argument('--idle-seconds', type=int, default=15)
    parser.add_argument('--phases', nargs='+', choices=('baseline', 'current', 'upgrade'), default=['baseline', 'current', 'upgrade'])
    args = parser.parse_args()
    if args.worker:
        worker(args.source, args.state)
        return
    import psutil
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=True)
    node = shutil.which('node')
    if not node or not os.environ.get('WORKSPACE_PLAYWRIGHT_PATH') or not os.environ.get('WORKSPACE_BROWSER_PATH'):
        raise SystemExit('Set WORKSPACE_PLAYWRIGHT_PATH and WORKSPACE_BROWSER_PATH; Node is required.')
    (ROOT / 'build').mkdir(exist_ok=True)
    results = []
    baseline_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    with tempfile.TemporaryDirectory(prefix='archive-browser-', dir=ROOT / 'build') as temporary:
        fixture = Path(temporary)
        baseline = fixture / 'baseline'
        baseline.mkdir()
        archive = fixture / 'baseline.zip'
        subprocess.run(['git', 'archive', '--format=zip', '--output=' + str(archive), baseline_commit], cwd=ROOT, check=True)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(baseline)
        for phase, source, mode in [('baseline', baseline, 'main'), ('current', ROOT, 'main'), ('upgrade', ROOT, 'upgrade')]:
            if phase not in args.phases:
                continue
            state = fixture / ('state-' + phase)
            state.mkdir()
            process = subprocess.Popen([sys.executable, '-X', 'utf8', '-B', str(Path(__file__).resolve()),
                '--worker', '--source', str(source), '--state', str(state)],
                cwd=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            child = None
            try:
                ready = json.loads(process.stdout.readline())
                options = {**ready, 'mode': mode, 'phase': phase, 'output': str(output),
                    'cycles': args.cycles, 'idleSeconds': args.idle_seconds,
                    'current': phase != 'baseline'}
                samples = []
                monitored = psutil.Process(process.pid)
                marker = output / (phase + '-stage.json')
                marker.unlink(missing_ok=True)
                child = subprocess.Popen([node, str(ROOT / 'scripts/test-lab/verify-archive-draft-browser-load.cjs')],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', cwd=ROOT)
                child.stdin.write(json.dumps(options))
                child.stdin.close()
                began = time.monotonic()
                while child.poll() is None:
                    stage = 'startup'
                    if marker.exists():
                        try:
                            stage = json.loads(marker.read_text(encoding='utf-8'))['stage']
                        except (OSError, ValueError):
                            pass
                    if process.poll() is None:
                        cpu = monitored.cpu_times()
                        samples.append({'seconds': round(time.monotonic() - began, 3), 'stage': stage,
                            'cpuSeconds': cpu.user + cpu.system, 'rssBytes': monitored.memory_info().rss})
                    if time.monotonic() - began > 150:
                        raise AssertionError('Browser verification exceeded 150 seconds')
                    time.sleep(.2)
                stdout, stderr = child.stdout.read(), child.stderr.read()
                assert child.returncode == 0, stderr[-5000:] or stdout[-3000:]
                browser = json.loads(stdout)
                process.wait(timeout=8)
                assert process.returncode == 0, process.stderr.read()[-3000:]
                drafts = json.loads((state / 'final-drafts.json').read_text(encoding='utf-8'))
                saved = {row['id']: row for row in drafts['entries']}
                if phase == 'current':
                    assert saved[ready['archived']]['draft']['text'] == '이미 저장된 초안'
                perf = {}
                for stage in ('idle-before', 'idle-after'):
                    group = [row for row in samples if row['stage'] == stage]
                    if len(group) > 1:
                        elapsed = group[-1]['seconds'] - group[0]['seconds']
                        cpu = group[-1]['cpuSeconds'] - group[0]['cpuSeconds']
                        perf[stage] = {'elapsedSeconds': round(elapsed, 3), 'cpuSeconds': round(cpu, 5),
                            'oneCoreCpuPercent': round(cpu / elapsed * 100, 3),
                            'rssStartBytes': group[0]['rssBytes'], 'rssEndBytes': group[-1]['rssBytes'],
                            'rssPeakBytes': max(row['rssBytes'] for row in group)}
                results.append({'phase': phase, 'browser': browser, 'serverPerformance': perf,
                    'serverSamples': samples, 'serverStopped': True, 'passed': True})
                (output / (phase + '-report.json')).write_text(json.dumps(results[-1], ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({'phase': phase, 'passed': True, 'idle': perf}), flush=True)
            finally:
                if child and child.poll() is None:
                    child.terminate()
                    child.wait(timeout=8)
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=8)
    report = {'passed': True, 'scope': 'Shipped full browser UI and real isolated LocalApp API; synthetic model replies; no native installer.',
        'baselineCommit': baseline_commit,
        'productionRequests': 0, 'personalStateUsed': False, 'installedApplicationChanged': False,
        'cyclesPerMainPhase': args.cycles, 'idleSecondsPerWindow': args.idle_seconds, 'results': results,
        'limits': 'Short controlled comparison on one PC. CPU/RSS vary with Windows scheduling and browser caches; this is not a long-duration leak or corporate hardware benchmark.'}
    (output / 'browser-load-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
