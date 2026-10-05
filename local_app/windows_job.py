"""Own Windows CLI descendants before any CLI code runs.

Only public Win32 APIs are used. An unnamed, non-inheritable job adds no
resource, UI or token limits and grants no breakaway; children inherit membership.
Keep its handle until the kernel confirms the entire job is empty, including
when the original wrapper has already exited. No process-name/PID termination.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import time

CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
TH32CS_SNAPTHREAD = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
THREAD_QUERY_LIMITED_INFORMATION = 0x0800
ERROR_NO_MORE_FILES = 18
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _BasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong),
                ('PerJobUserTimeLimit', ctypes.c_longlong),
                ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimits), ('IoInfo', _IoCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_longlong) for name in (
        'TotalUserTime', 'TotalKernelTime', 'ThisPeriodTotalUserTime', 'ThisPeriodTotalKernelTime')]
    _fields_ += [(name, wintypes.DWORD) for name in (
        'TotalPageFaultCount', 'TotalProcesses', 'ActiveProcesses', 'TotalTerminatedProcesses')]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ThreadID', wintypes.DWORD), ('th32OwnerProcessID', wintypes.DWORD),
                ('tpBasePri', wintypes.LONG), ('tpDeltaPri', wintypes.LONG),
                ('dwFlags', wintypes.DWORD)]


class _WinAPI:
    def __init__(self):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': (wintypes.HANDLE, [ctypes.c_void_p, wintypes.LPCWSTR]),
            'SetInformationJobObject': (wintypes.BOOL, [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]),
            'AssignProcessToJobObject': (wintypes.BOOL, [wintypes.HANDLE, wintypes.HANDLE]),
            'QueryInformationJobObject': (wintypes.BOOL, [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]),
            'TerminateJobObject': (wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT]),
            'CreateToolhelp32Snapshot': (wintypes.HANDLE, [wintypes.DWORD, wintypes.DWORD]),
            'Thread32First': (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]),
            'Thread32Next': (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]),
            'OpenThread': (wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
            'GetProcessIdOfThread': (wintypes.DWORD, [wintypes.HANDLE]),
            'ResumeThread': (wintypes.DWORD, [wintypes.HANDLE]),
            'CloseHandle': (wintypes.BOOL, [wintypes.HANDLE]),
        }
        for name, (restype, argtypes) in signatures.items():
            function = getattr(self.kernel, name)
            function.restype, function.argtypes = restype, argtypes
            setattr(self, name, function)


def _error():
    return ctypes.WinError(ctypes.get_last_error())


class WindowsJob:
    """A single bridge's retained ownership handle; its closer serializes use."""

    def __init__(self):
        self._api = _WinAPI()
        self._handle = self._api.CreateJobObjectW(None, None)
        self.assigned = False
        if not self._handle:
            raise _error()
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._api.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = _error()
            self._api.CloseHandle(self._handle)
            self._handle = None
            raise error

    def assign_and_resume(self, process):
        # CPython 3.11+ retains the original CreateProcess handle in Popen.
        # Use that exact handle, never reopen a possibly recycled process ID.
        handle = int(process._handle)
        if process.poll() is not None:
            raise OSError('The suspended CLI exited before job assignment.')
        if not self._api.AssignProcessToJobObject(self._handle, handle):
            raise _error()
        self.assigned = True
        self._resume_primary_thread(process.pid)

    def _resume_primary_thread(self, pid):
        # Popen closes CreateProcess's primary-thread handle. Before any code
        # runs there must be exactly one thread. Never guess the primary when
        # an external injector or debugger has introduced additional threads.
        snapshot = self._api.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            raise _error()
        candidates = []
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = self._api.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.dwSize < _ThreadEntry.th32OwnerProcessID.offset + ctypes.sizeof(wintypes.DWORD):
                    raise OSError('Incomplete Windows thread ownership information.')
                if entry.th32OwnerProcessID == pid:
                    candidates.append(entry.th32ThreadID)
                entry.dwSize = ctypes.sizeof(entry)
                found = self._api.Thread32Next(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != ERROR_NO_MORE_FILES:
                raise _error()
        finally:
            self._api.CloseHandle(snapshot)
        if len(candidates) != 1:
            raise OSError('Cannot identify the suspended CLI primary thread.')
        thread = self._api.OpenThread(THREAD_SUSPEND_RESUME | THREAD_QUERY_LIMITED_INFORMATION,
                                      False, candidates[0])
        if not thread:
            raise _error()
        try:
            if self._api.GetProcessIdOfThread(thread) != pid:
                raise OSError('The suspended CLI thread ownership changed.')
            previous_count = self._api.ResumeThread(thread)
            if previous_count == 0xFFFFFFFF:
                raise _error()
            if previous_count != 1:
                raise OSError('Unexpected suspended CLI thread state.')
        finally:
            self._api.CloseHandle(thread)

    def active_processes(self):
        if self._handle is None:
            return 0  # Released only after a successful empty-job check.
        accounting = _Accounting()
        if not self._api.QueryInformationJobObject(self._handle, 1, ctypes.byref(accounting),
                                                   ctypes.sizeof(accounting), None):
            raise _error()
        return accounting.ActiveProcesses

    def wait_empty(self, timeout):
        deadline = time.monotonic() + timeout
        while self.active_processes():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(.05, remaining))
        return True

    def terminate(self):
        if self._handle is not None and not self._api.TerminateJobObject(self._handle, 1):
            error = _error()
            # Natural exit racing termination is success only with exact
            # kernel membership evidence, never just the wrapper's exit code.
            if self.active_processes():
                raise error

    def release_empty(self):
        if self._handle is None:
            return
        if self.active_processes():
            raise OSError('The owned CLI job still contains active processes.')
        if not self._api.CloseHandle(self._handle):
            raise _error()
        self._handle = None
