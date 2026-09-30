"""One dedicated WebView2 window, owned by this server through private pipes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
import unicodedata


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
        # The reader must never wait for the command lock while the caller waits
        # for its acknowledgement. Only one visible card/callback is retained.
        self.notification_lock = threading.Lock()
        self._notification_capable = False
        self._notification = None

    @property
    def notification_available(self):
        with self.notification_lock:
            return bool(not self.closed and self._notification_capable
                        and self.process is not None and self.process.poll() is None
                        and self.reader is not None and self.reader.is_alive())

    def _clear_notification(self, process):
        with self.notification_lock:
            if self._notification and self._notification[0] is process:
                self._notification = None
            if self.process is process:
                self._notification_capable = False

    def _notification_event(self, process, event):
        with self.notification_lock:
            pending = self._notification
            if (self.closed or process is not self.process or not pending
                    or pending[0] is not process or event.get('notificationId') != pending[1]):
                return
            self._notification = None
        if event.get('type') == 'notification_opened':
            def opened():
                # A delayed click from a retired host must not reopen the app.
                if self.closed or self.process is not process:
                    return
                try:
                    pending[2]()
                except Exception:
                    pass  # Notification navigation must not stop IPC delivery.
            threading.Thread(target=opened, name='workspace-notification-open', daemon=True).start()

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
                elif kind in {'notification_opened', 'notification_dismissed'}:
                    self._notification_event(process, event)
                elif kind == 'close_requested' and self.on_close and not self.closed:
                    threading.Thread(target=self.on_close, name='workspace-desktop-close', daemon=True).start()
                if process is self.process:
                    self.last_event = kind
        except (OSError, ValueError, queue.Full):
            pass
        finally:
            self._clear_notification(process)
            try:
                responses.put_nowait({'type': 'exited'})
            except queue.Full:
                pass

    def _write(self, value):
        self.process.stdin.write(json.dumps(value, ensure_ascii=True) + '\n')
        self.process.stdin.flush()

    def _command(self, command, **payload):
        self.sequence += 1
        sequence = self.sequence
        self._write({**payload, 'command': command, 'id': sequence})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            event = self.responses.get(timeout=max(.01, deadline - time.monotonic()))
            if event.get('type') == 'exited':
                raise DesktopError()
            if event.get('type') == 'ack' and event.get('id') == sequence:
                if event.get('ok') is not True:
                    raise DesktopError()
                return event
        raise DesktopError()

    def notify(self, *, title, message, kind, notification_id, on_click):
        """Offer a card without starting a host; None alone permits fallback."""
        if (not isinstance(notification_id, str) or not re.fullmatch(r'[0-9a-f]{64}', notification_id)
                or not isinstance(kind, str) or kind not in {'completed', 'attention', 'error'}
                or not isinstance(title, str) or not isinstance(message, str) or not callable(on_click)):
            return False
        def clean(value, limit):
            return ''.join(' ' if unicodedata.category(char).startswith('C') else char
                           for char in value).strip()[:limit]
        with self.lock:
            if not self.notification_available:
                return None
            process = self.process
            pending = (process, notification_id, on_click)
            with self.notification_lock:
                if self._notification is not None:
                    return False
                self._notification = pending
            try:
                reply = self._command('notify', notificationId=notification_id, kind=kind,
                                      title=clean(title, 100), message=clean(message, 255))
                accepted = reply.get('notificationAccepted') is True
            except (OSError, ValueError, queue.Empty):
                # The card may already be visible after an acknowledgement was
                # lost. Do not cause a second Windows balloon in that case.
                accepted = False
            if not accepted:
                with self.notification_lock:
                    if self._notification is pending:
                        self._notification = None
            return accepted

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
                with self.notification_lock:
                    self._notification_capable = ready.get('notificationCards') is True
                return {'ok': True, 'action': 'opened', **self.presentation}
            except (OSError, ValueError, queue.Empty) as exc:
                self._dispose()
                if isinstance(exc, DesktopError):
                    raise
                raise DesktopError() from exc

    def _dispose(self):
        process, reader = self.process, self.reader
        with self.notification_lock:
            self._notification_capable = False
            self._notification = None
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
