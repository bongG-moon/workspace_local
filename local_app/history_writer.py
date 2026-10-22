"""Coalesce display-only history saves without holding the application lock.

Queue delivery, user edits and terminal saves remain synchronous. Tokens make
their newer snapshots supersede queued work; HistoryStore serializes disk writes.
"""
import threading
import time


class ProgressHistoryWriter:
    def __init__(self, snapshot, write, completed, *, delay=.25):
        self.snapshot, self.write, self.completed = snapshot, write, completed
        self.delay = delay
        self.condition = threading.Condition()
        self.pending = {}
        self.tokens = {}
        self.failures = set()
        self.inflight = None
        self.thread = None
        self.sealed = False

    def defer(self, sid):
        with self.condition:
            if self.sealed:
                return False
            token = object()
            due = self.pending.get(sid, (None, time.monotonic() + self.delay))[1]
            self.tokens[sid] = token
            self.pending[sid] = (token, due)
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name='workspace-history', daemon=True)
                self.thread.start()
            self.condition.notify_all()
            return True

    def current(self, sid, token):
        with self.condition:
            return self.tokens.get(sid) is token

    def invalidate(self, sid=None):
        # Called before synchronous storage while the app lock is held. Never
        # wait for a worker here: snapshot() may be waiting for that same lock.
        with self.condition:
            for key in list(self.tokens) if sid is None else (sid,):
                self.tokens.pop(key, None)
                self.pending.pop(key, None)
            self.condition.notify_all()

    def saved(self, sid=None):
        with self.condition:
            if sid is None:
                self.failures.clear()
            else:
                self.failures.discard(sid)

    def failed_save(self, session_ids):
        with self.condition:
            self.failures.update(session_ids)

    def seal(self):
        with self.condition:
            self.sealed = True
            self.condition.notify_all()

    def _run(self):
        while True:
            with self.condition:
                while not self.pending:
                    if self.sealed:
                        return
                    self.condition.wait()
                sid = min(self.pending, key=lambda key: self.pending[key][1])
                token, due = self.pending[sid]
                remaining = due - time.monotonic()
                if remaining > 0:
                    self.condition.wait(remaining)
                    continue
                self.pending.pop(sid)
                self.inflight = (sid, token)
            error = None
            written = False
            try:
                value = self.snapshot(sid, token)
                if value is not None:
                    written = self.write(value, lambda: self.current(sid, token))
            except Exception as exc:
                error = exc
            finally:
                # Completion acquires the app lock only after releasing storage.
                self.completed(sid, token, written, error)
                with self.condition:
                    if self.tokens.get(sid) is token:
                        if error is not None:
                            self.failures.add(sid)
                        else:
                            self.failures.discard(sid)
                        self.tokens.pop(sid, None)
                    self.inflight = None
                    self.condition.notify_all()

    def flush(self, timeout=5.0, *, seal=False):
        deadline = time.monotonic() + timeout
        with self.condition:
            if seal:
                self.sealed = True
            self.pending = {sid: (token, 0) for sid, (token, _) in self.pending.items()}
            self.condition.notify_all()
            while self.pending or self.inflight is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            success = not self.failures
            thread = self.thread if self.sealed else None
        if thread is not None:
            thread.join(max(0, deadline - time.monotonic()))
            success = success and not thread.is_alive()
        return success

    def failed(self):
        with self.condition:
            return bool(self.failures)

    def failed_sessions(self):
        with self.condition:
            return list(self.failures)
