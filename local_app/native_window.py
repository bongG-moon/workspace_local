"""One dedicated WebView2 window, owned by this server through private pipes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time


class DesktopError(OSError):
    def __init__(self, code=47):
        self.code = code if code in (46, 47) else 47
        super().__init__('WebView2 Runtime을 찾지 못했습니다. PC의 실행 조건을 확인해 주세요.' if self.code == 46
                         else '전용 앱 창을 준비하지 못했습니다. 앱 파일과 WebView2 실행 정책을 확인해 주세요.')


def desktop_executable():
    root = Path(__file__).resolve().parents[1]
    bundled = root / 'desktop/Workspace.Desktop.exe'
    if bundled.is_file():
        return bundled.resolve()
    # Explicit source checkout build, never a different installation or PATH.
    return (root / 'build/desktop-host/Workspace.Desktop.exe').resolve()


class DesktopHost:
    def __init__(self, notifier, url, state, *, background=False, on_close=None, popen=subprocess.Popen):
        self.notifier, self.url = notifier, url
        self.profile = str((Path(state).resolve() / 'webview2').resolve())
        self.background, self.on_close, self.popen = background, on_close, popen
        self.lock = threading.RLock()
        self.process = None
        self.reader = None
        self.responses = queue.Queue(maxsize=32)
        self.closed = False
        self.sequence = 0
        self.presentation = {'mode': 'desktop', 'engine': 'WebView2'}
        self.last_event = None

    def _read(self, process, responses):
        try:
            while True:
                line = process.stdout.readline(16385)
                if not line or len(line) > 16384:
                    break
                event = json.loads(line)
                if not isinstance(event, dict):
                    break
                kind = event.get('type')
                if kind in {'ready', 'ack', 'error'}:
                    responses.put_nowait(event)
                elif kind == 'close_requested' and self.on_close and not self.closed:
                    threading.Thread(target=self.on_close, name='workspace-desktop-close', daemon=True).start()
                if process is self.process:
                    self.last_event = kind
        except (OSError, ValueError, queue.Full):
            pass
        finally:
            try:
                responses.put_nowait({'type': 'exited'})
            except queue.Full:
                pass

    def _write(self, value):
        self.process.stdin.write(json.dumps(value, ensure_ascii=True) + '\n')
        self.process.stdin.flush()

    def _command(self, command):
        self.sequence += 1
        sequence = self.sequence
        self._write({'command': command, 'id': sequence})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            event = self.responses.get(timeout=max(.01, deadline - time.monotonic()))
            if event.get('type') == 'exited':
                raise DesktopError()
            if event.get('type') == 'ack' and event.get('id') == sequence:
                if event.get('ok') is not True:
                    raise DesktopError()
                return
        raise DesktopError()

    def open(self):
        with self.lock:
            if self.closed:
                raise DesktopError()
            if self.process is not None and self.process.poll() is None:
                try:
                    self._command('activate')
                    return {'ok': True, 'action': 'activated', **self.presentation}
                except (OSError, ValueError, queue.Empty) as exc:
                    # A live but unresponsive window must not create a duplicate.
                    raise DesktopError() from exc
            self._dispose()
            executable = desktop_executable()
            if os.name != 'nt' or not executable.is_file():
                raise DesktopError()
            try:
                self.responses = queue.Queue(maxsize=32)
                self.process = self.popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), cwd=str(executable.parent))
                self.reader = threading.Thread(target=self._read, args=(self.process, self.responses),
                                               name='workspace-desktop-events', daemon=True)
                self.reader.start()
                self._write({'url': self.url, 'profile': self.profile, 'background': self.background})
                ready = self.responses.get(timeout=30)
                if ready.get('type') != 'ready' or ready.get('pid') != self.process.pid:
                    raise DesktopError(ready.get('code', 47))
                hwnd = ready.get('hwnd')
                if not isinstance(hwnd, int) or hwnd <= 0:
                    raise DesktopError()
                if not self.notifier.bind_owned(hwnd, self.process.pid):
                    raise DesktopError()
                self.presentation['runtime'] = str(ready.get('runtime', ''))[:100]
                return {'ok': True, 'action': 'opened', **self.presentation}
            except (OSError, ValueError, queue.Empty) as exc:
                self._dispose()
                if isinstance(exc, DesktopError):
                    raise
                raise DesktopError() from exc

    def _dispose(self):
        process, reader = self.process, self.reader
        self.process, self.reader = None, None
        if process is not None:
            try:
                if process.poll() is None:
                    process.stdin.write('{"command":"close","id":0}\n')
                    process.stdin.flush()
            except (OSError, ValueError):
                pass
            try:
                process.stdin.close()  # EOF closes this host when the server exits.
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()  # Only the exact owned subprocess, no PID search.
                process.wait(timeout=5)
            except (OSError, ValueError):
                pass
            if reader is not None and reader is not threading.current_thread():
                reader.join(timeout=2)
            process.stdout.close()

    def close(self):
        with self.lock:
            self.closed = True
            self._dispose()
