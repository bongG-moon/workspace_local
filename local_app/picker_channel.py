"""Private per-request native-picker channel, including older Python on Windows."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from .windows_paths import local_app_data_folder


def _create_private_directory(path: Path):
    """Create with an explicit protected DACL; never set ACL after creation."""
    class TokenUser(ctypes.Structure):
        _fields_ = [('sid', ctypes.c_void_p), ('attributes', wintypes.DWORD)]

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('descriptor', ctypes.c_void_p),
                    ('inherit', wintypes.BOOL)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [ctypes.c_wchar_p, wintypes.DWORD,
                                                                            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    kernel.CreateDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(SecurityAttributes)]
    token, sid_string, descriptor = wintypes.HANDLE(), ctypes.c_void_p(), ctypes.c_void_p()
    try:
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        required = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))
        if not required.value or required.value > 65536:
            raise OSError('Unable to identify the picker channel owner.')
        buffer = ctypes.create_string_buffer(required.value)
        if not security.GetTokenInformation(token, 1, buffer, len(buffer), ctypes.byref(required)):
            raise ctypes.WinError(ctypes.get_last_error())
        owner = ctypes.cast(buffer, ctypes.POINTER(TokenUser)).contents
        if not security.ConvertSidToStringSidW(owner.sid, ctypes.byref(sid_string)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.wstring_at(sid_string.value)
        # Only this user and SYSTEM; files inherit these rules. Parent defaults
        # and Python's version-dependent Windows mkdir permissions do not apply.
        sddl = 'D:P(A;OICI;FA;;;' + sid + ')(A;OICI;FA;;;SY)'
        if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor.value, False)
        if not kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if descriptor.value:
            kernel.LocalFree(descriptor)
        if sid_string.value:
            kernel.LocalFree(sid_string)
        if token.value:
            kernel.CloseHandle(token)


@contextmanager
def private_picker_directory():
    if os.name != 'nt':
        with tempfile.TemporaryDirectory(prefix='workspace-picker-') as directory:
            yield Path(directory)
        return
    # A shared TEMP may grant DELETE_CHILD to other users even for a child with
    # a private DACL. Use the OS-known user directory rather than trusting TEMP.
    parent = local_app_data_folder()
    for attempt in range(3):
        directory = parent / ('workspace-picker-' + uuid.uuid4().hex)
        try:
            _create_private_directory(directory)
            break
        except FileExistsError:
            if attempt == 2:
                raise
    try:
        yield directory
    finally:
        # Refuse a substituted reparse root instead of following it on cleanup.
        info = directory.lstat()
        if (directory.parent != parent or directory.resolve(strict=True) != directory
                or getattr(info, 'st_file_attributes', 0) & 0x400 or directory.is_symlink()):
            raise OSError('Picker channel location changed; cleanup stopped.')
        shutil.rmtree(directory)
