"""Isolated cold-profile asset diagnostics; never control existing windows.

The recorder keeps only allowlisted static paths, status, count and timing.
Authorization, queries, request/response bodies and raw exceptions are omitted.
Native mode requires --native; --http-only never launches a window. State is
retained under this checkout's build directory and each trial uses a new profile.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import http.client
import json
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from local_app.attention import AttentionNotifier
from local_app.native_window import DesktopHost, DesktopError
from local_app.server import LocalApp, Server, Handler
from local_app.ui_health import MODULES, STATES

ASSETS = frozenset({
    '/', '/app.js', '/startup-health.js', '/rich-content.js', '/execution-view.js',
    '/tool-activity.js', '/tool-activity.css', '/path-picker.js', '/path-picker.css',
    '/upgrade-handoff.js', '/upgrade-handoff.css',
    '/rendering.js', '/attachments.js', '/workflow.js', '/composer.js',
    '/inline-controls.js', '/attention.js', '/desktop.js', '/session-import.js',
    '/capabilities.js', '/productivity.js', '/palette.js', '/layout.js',
    '/app.css', '/productivity.css', '/review.css', '/startup-health.css',
    '/rich-content.css', '/execution-view.css', '/fonts/NotoSansKR-Variable.woff',
    '/favicon.ico', '/icon.svg', '/app-icon-192.png', '/app-icon-512.png',
})


class RecordingHandler(Handler):
    def send_response(self, code, message=None):
        self._asset_status = int(code)
        return super().send_response(code, message)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path not in ASSETS:
            return super().do_GET()
        began = time.monotonic()
        self._asset_status = None
        try:
            return super().do_GET()
        finally:
            ended = time.monotonic()
            with self.server.record_lock:
                self.server.counts[path] = self.server.counts.get(path, 0) + 1
                self.server.asset_records.append({
                    'path': path, 'status': self._asset_status,
                    'count': self.server.counts[path],
                    'startedMs': round((began - self.server.began) * 1000, 2),
                    'durationMs': round((ended - began) * 1000, 2),
                })


class RecordingServer(Server):
    def __init__(self, app, backlog):
        # None deliberately inherits the production Server setting.
        if backlog is not None:
            self.request_queue_size = backlog
        self.began = time.monotonic()
        self.record_lock = threading.Lock()
        self.asset_records, self.counts = [], {}
        super().__init__(app)
        self.RequestHandlerClass = RecordingHandler

    def handle_error(self, request, client_address):
        # The static recorder supplies the bounded status/timing evidence.
        # Never let the base handler dump raw exception or request context.
        pass


def health_summary(state):
    try:
        value = json.loads((state / 'ui-diagnostics.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        value = {}
    startups, failed_modules, native_failures = [], [], {}
    for row in value.get('events', []):
        module = row.get('module')
        if row.get('event') == 'startup' and row.get('status') in STATES:
            startups.append({'status': row['status'], 'elapsedMs': row.get('elapsedMs'),
                             'missing': [name for name in row.get('missing', []) if name in MODULES]})
        if (row.get('event') == 'module' and row.get('reason') == 'resource-error'
                and module in MODULES):
            failed_modules.append(module)
        if module == 'native' and row.get('event') in {'process_failed', 'navigation_failed', 'error'}:
            kind = row['event']
            native_failures[kind] = native_failures.get(kind, 0) + 1
    return {'startup': startups, 'resourceFailures': sorted(set(failed_modules)),
            'startupReady': any(row['status'] == 'ready' for row in startups),
            'nativeFailureCounts': native_failures}


def static_burst(server):
    paths = sorted(path for path in ASSETS if path.endswith(('.js', '.css')))
    def get_asset(index):
        path = paths[index % len(paths)]
        began = time.monotonic()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        status = None
        try:
            connection.request('GET', path)
            response = connection.getresponse()
            status = response.status
            response.read()
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        return {'path': path, 'status': status, 'count': index + 1,
                'durationMs': round((time.monotonic() - began) * 1000, 2)}
    with ThreadPoolExecutor(max_workers=15) as pool:
        return list(pool.map(get_asset, range(45)))


def run_trial(output, backlog, number, native, wait_seconds, startup_order):
    backlog_label = 'production' if backlog is None else str(backlog)
    state = output / f'backlog-{backlog_label}-trial-{number}'
    state.mkdir(exist_ok=False)
    app = LocalApp(state, demo=True)
    server = RecordingServer(app, backlog)
    thread = None
    host = None
    result = {'backlog': server.request_queue_size, 'requestedBacklog': backlog_label,
              'startupOrder': startup_order, 'trial': number, 'native': native}

    def start_serving():
        app.dispatch.start()
        worker = threading.Thread(target=server.serve_forever,
                                  kwargs={'poll_interval': .3}, daemon=True)
        worker.start()
        return worker

    try:
        if startup_order == 'serve-before-window':
            thread = start_serving()
        if native:
            app.notifier = AttentionNotifier(enabled=True)
            host = DesktopHost(app.notifier, server.origin + '/#token=' + app.token,
                               state, background=True, on_event=app.ui_health.native)
            app._desktop_window = host
            app._open_window_callback = host.open
            # Keep both orders available to reproduce the earlier launch path.
            result['opened'] = host.open()['action'] == 'opened'
        if thread is None:
            thread = start_serving()
        if native:
            threading.Event().wait(wait_seconds)
            result.update(health_summary(state))
        else:
            result['clientRequests'] = static_burst(server)
    except DesktopError as exc:
        result['nativeErrorCode'] = exc.code
    except Exception as exc:
        result['fixtureErrorType'] = type(exc).__name__
    finally:
        # DesktopHost.close targets its own Popen child and verified IPC only.
        if host:
            process = host.process
            host.close()
            result['ownedHostClosed'] = process is None or process.poll() is not None
        app.close()
        if thread is not None:
            server.shutdown()
            thread.join(3)
        server.server_close()
        with server.record_lock:
            result['serverRequests'] = list(server.asset_records)
        result['requestCount'] = len(result['serverRequests'])
        result['non200Count'] = sum(row['status'] != 200 for row in result['serverRequests'])
        if 'clientRequests' in result:
            result['clientNon200Count'] = sum(row['status'] != 200 for row in result['clientRequests'])
        (state / 'asset-report.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items()
                      if key not in {'serverRequests', 'clientRequests'}}, ensure_ascii=True), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--native', action='store_true')
    mode.add_argument('--http-only', action='store_true')
    parser.add_argument('--trials', type=int, choices=range(1, 9), default=4)
    parser.add_argument('--wait-seconds', type=float, default=10)
    parser.add_argument('--backlog', choices=('production', '5', '64'), default='production')
    parser.add_argument('--startup-order', choices=('ready-before-serve', 'serve-before-window'),
                        default='ready-before-serve')
    parser.add_argument('--compare-backlog', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.wait_seconds <= 30:
        parser.error('--wait-seconds must be between 1 and 30')
    output = ROOT / 'build' / ('native-startup-assets-' + datetime.now().strftime('%Y%m%d-%H%M%S')
                             + '-' + uuid.uuid4().hex[:6])
    output.mkdir(parents=True, exist_ok=False)
    print(json.dumps({'reportDirectory': str(output), 'native': args.native}), flush=True)
    backlog = None if args.backlog == 'production' else int(args.backlog)
    results = [run_trial(output, backlog, index + 1, args.native, args.wait_seconds, args.startup_order)
               for index in range(args.trials)]
    # A renderer process failure before document execution is a different
    # boundary; a backlog comparison cannot diagnose that native failure.
    failed = any(row.get('resourceFailures') or row['non200Count']
                 or row.get('clientNon200Count') for row in results)
    if args.compare_backlog and failed and results[0]['backlog'] != 64:
        results.extend(run_trial(output, 64, index + 1, args.native, args.wait_seconds, args.startup_order)
                       for index in range(args.trials))
    report = {'native': args.native, 'baselineFailure': failed,
              'unreadyTrials': sum(args.native and not row.get('startupReady') for row in results),
              'trials': results}
    (output / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'summary': str(output / 'summary.json'), 'trials': len(results),
                      'baselineFailure': failed}), flush=True)


if __name__ == '__main__':
    main()
