"""Resolve Windows helpers without trusting the working directory or PATH."""
import ctypes
from pathlib import Path


def powershell_path() -> str:
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    get_directory = kernel.GetSystemDirectoryW
    get_directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    get_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_directory(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError('Windows system directory is unavailable.')
    executable = Path(buffer.value) / 'WindowsPowerShell/v1.0/powershell.exe'
    if not executable.is_absolute() or not executable.is_file():
        raise OSError('Windows PowerShell is unavailable.')
    return str(executable)
