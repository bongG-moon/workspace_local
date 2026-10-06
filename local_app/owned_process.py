"""Bound short local helpers and retain ownership of their Windows descendants.

Captured output is spooled to temporary files so an exited wrapper cannot leave
communicate() waiting on an inherited pipe. The complete owned Windows job is
retired even after a successful command. This runner is for probes and native
dialogs, never long-lived CLI sessions or programs opened for the user.
"""
from __future__ import annotations

from contextlib import ExitStack
import os
import subprocess
import tempfile
import time

if __package__:
    from .windows_job import CREATE_SUSPENDED, WindowsJob
else:
    from windows_job import CREATE_SUSPENDED, WindowsJob

POLL_INTERVAL = .05
CLEANUP_TIMEOUT = 5
DEFAULT_OUTPUT_LIMIT = 2 * 1024 * 1024


class CancelledError(subprocess.SubprocessError):
    def __init__(self):
        super().__init__('The local helper was cancelled.')


class OutputLimitExceeded(subprocess.SubprocessError):
    def __init__(self):
        super().__init__('The local helper returned too much output.')


def _cleanup(process, job):
    """Always release kernel handles; never search or terminate by image name."""
    try:
        if job is not None and job.assigned:
            job.terminate()
            if not job.wait_empty(CLEANUP_TIMEOUT):
                raise OSError('The owned local helper did not finish terminating.')
        elif process is not None and process.poll() is None:
            # Job assignment failed before the suspended child's first
            # instruction, or this is the non-Windows direct-child fallback.
            process.kill()
        if process is not None:
            process.wait(timeout=CLEANUP_TIMEOUT)
        if job is not None:
            job.release_empty()
    finally:
        if job is not None:
            # Query/termination failures still must not abandon an owning
            # handle. KILL_ON_JOB_CLOSE is the final exact-ownership fallback.
            job.close_handle()
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=CLEANUP_TIMEOUT)


def run_owned(args, *, timeout, cancel_event=None, output_limit=DEFAULT_OUTPUT_LIMIT,
              capture_output=False, input=None, check=False, **kwargs):
    """Run a finite helper with subprocess.run-compatible capture/text options.

Cancellation is checked every 50 ms. Hidden helpers receive EOF on stdin by
default. A bounded input may be supplied without creating a writer thread.
Windows children are assigned to an unnamed kill-on-close job before any code
runs; inability to establish ownership aborts the launch. At most output_limit
bytes per captured stream are read into memory. Existing external output file
handles are honored, and are owned by the caller.
"""
    if timeout <= 0 or output_limit <= 0:
        raise ValueError('A positive helper timeout and output limit are required.')
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError()
    if kwargs.get('shell'):
        raise ValueError('Local helpers require an explicit executable argument list.')
    if capture_output:
        if kwargs.get('stdout') is not None or kwargs.get('stderr') is not None:
            raise ValueError('stdout and stderr cannot be combined with capture_output.')
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    text_option = kwargs.pop('text', False)
    universal_newlines = kwargs.pop('universal_newlines', False)
    text_mode = bool(text_option or universal_newlines)
    encoding = kwargs.pop('encoding', None)
    errors = kwargs.pop('errors', None)
    text_mode = text_mode or encoding is not None or errors is not None
    encoding = encoding or 'utf-8'
    errors = errors or 'strict'
    flags = kwargs.pop('creationflags', 0)
    with ExitStack() as files:
        captured = {}
        for name in ('stdout', 'stderr'):
            if kwargs.get(name) == subprocess.PIPE:
                captured[name] = files.enter_context(tempfile.TemporaryFile('w+b'))
                kwargs[name] = captured[name]
            elif kwargs.get(name) is None:
                kwargs[name] = subprocess.DEVNULL
        if input is not None:
            if kwargs.get('stdin') is not None:
                raise ValueError('stdin cannot be combined with input.')
            source = files.enter_context(tempfile.TemporaryFile('w+b'))
            source.write(input.encode(encoding, errors) if text_mode else input)
            source.seek(0)
            kwargs['stdin'] = source
        elif kwargs.get('stdin') is None:
            kwargs['stdin'] = subprocess.DEVNULL
        elif kwargs['stdin'] == subprocess.PIPE:
            raise ValueError('Use input instead of an interactive stdin pipe for local helpers.')
        job = WindowsJob() if os.name == 'nt' else None
        process = None
        try:
            process = subprocess.Popen(args, creationflags=flags | (CREATE_SUSPENDED if job else 0), **kwargs)
            if job is not None:
                job.assign_and_resume(process)
            deadline = time.monotonic() + timeout
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise CancelledError()
                if any(os.fstat(stream.fileno()).st_size > output_limit for stream in captured.values()):
                    raise OutputLimitExceeded()
                if process.poll() is not None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                delay = min(POLL_INTERVAL, remaining)
                if cancel_event is not None:
                    cancel_event.wait(delay)
                else:
                    time.sleep(delay)
        finally:
            _cleanup(process, job)
        values = {}
        for name, stream in captured.items():
            stream.seek(0)
            value = stream.read(output_limit + 1)
            if len(value) > output_limit:
                raise OutputLimitExceeded()
            values[name] = value.decode(encoding, errors).replace('\r\n', '\n').replace('\r', '\n') if text_mode else value
        result = subprocess.CompletedProcess(args, process.returncode, values.get('stdout'), values.get('stderr'))
        if check:
            result.check_returncode()
        return result
