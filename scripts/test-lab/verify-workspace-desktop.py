"""Opt-in Windows integration: owned host/tray only, synthetic work, no Claude.

No other application's window or profile is inspected or changed.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from local_app.attention import AttentionNotifier
from local_app.native_window import DesktopHost
from local_app.server import LocalApp, Server
from local_app.tray import WorkspaceTray


def eventually(check, seconds=25):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check(): return
        time.sleep(.1)
    raise AssertionError('Owned desktop integration condition timed out.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--missing-runtime', action='store_true')
    args = parser.parse_args()
    state = args.state.resolve()
    state.mkdir(parents=True, exist_ok=False)
    app = LocalApp(state, demo=True)
    app.notifier = AttentionNotifier(enabled=True)
    server = Server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def launch(command, **kwargs):
        if args.missing_runtime:
            env = os.environ.copy()
            env['WEBVIEW2_BROWSER_EXECUTABLE_FOLDER'] = str(state/'absent-runtime')
            kwargs['env'] = env
        return subprocess.Popen(command, **kwargs)
    window = DesktopHost(app.notifier, server.origin + '/#token=' + app.token, state, popen=launch)
    app._desktop_window = window
    app._open_window_callback = window.open
    tray = WorkspaceTray(str(state), window.open, lambda: False)
    app.tray = tray
    report = {}
    try:
        if args.missing_runtime:
            from local_app.native_window import DesktopError
            try:
                window.open()
                raise AssertionError('Missing runtime was accepted')
            except DesktopError as error:
                assert error.code == 46, error.code
                report = {'missingRuntimeCode': error.code, 'hostClosed': window.process is None,
                          'runtimeInstalledOrChanged': False, 'browserFallback': False}
                (state/'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                print(json.dumps(report))
                return
        tray.start()
        window.background = tray.available
        report['initial'] = window.open()
        process = window.process
        eventually(lambda: window.last_event == 'loaded')
        report['pageLoaded'] = True
        binding = app.notifier._binding
        assert binding.pid == process.pid
        report['ownedBinding'] = app.notifier.native_state
        report['windowTheme'] = app.notifier.theme_state
        for _ in range(4):
            assert window.open()['action'] == 'activated'
            assert window.process is process
        report['repeatedOpensSameProcess'] = True
        if tray.available:
            # Send X only to the revalidated child HWND we just launched.
            native = app.notifier._native
            assert native.owned(binding.hwnd, process.pid) == binding
            from ctypes import wintypes as wt
            post = native.user.PostMessageW
            post.argtypes, post.restype = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL
            assert post(binding.hwnd, 0x0112, 0xF060, 0)  # WM_SYSCOMMAND / SC_CLOSE (titlebar X)
            eventually(lambda: not native.user.IsWindowVisible(binding.hwnd))
            assert process.poll() is None
            assert window.open()['action'] == 'activated'
            assert native.user.IsWindowVisible(binding.hwnd)
            report['closeHidesAndReopenRestores'] = True
        app.close()
        window.close()
        assert process.poll() is not None
        report['hostExited'] = True
        report['claudeStarted'] = False
        (state / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))
    finally:
        app.close()
        window.close()
        tray.stop()
        server.shutdown()
        server.server_close()
        thread.join(3)


if __name__ == '__main__': main()
