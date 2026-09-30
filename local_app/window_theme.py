"""Best-effort per-window chrome colors; never writes Windows or browser settings.

The caller must validate the HWND identity immediately before apply(). Windows
10 and browsers drawing their own caption may keep their original appearance.
Color attributes are documented for Set, not Get. Success only means Windows
accepted the requests, not that the browser visibly rendered them. On a partial
failure, reset only attributes changed here to Windows' default color behavior;
the unsupported color getter cannot capture an earlier custom window color.
"""
import ctypes
import os

PALETTE = {35: 0xF6E7E9, 36: 0x5C3239, 34: 0xEED8DC}  # caption, text, border COLORREF
DEFAULT_COLOR = 0xFFFFFFFF


def apply(hwnd):
    if os.name != 'nt' or not hwnd:
        return {'applied': False, 'reason': 'unsupported'}
    from ctypes import wintypes as wt
    changed = []
    setter = None

    def reset_changed():
        # No registry, browser preference, or unrelated window is changed.
        # Reset is also best effort when the HWND has already been destroyed.
        for attribute in changed:
            try:
                value = wt.DWORD(DEFAULT_COLOR)
                setter(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
            except (OSError, AttributeError, ValueError):
                pass

    try:
        user = ctypes.WinDLL('user32', use_last_error=True)
        class HighContrast(ctypes.Structure):
            _fields_ = [('cbSize', wt.UINT), ('dwFlags', wt.DWORD), ('scheme', wt.LPWSTR)]
        contrast = HighContrast(ctypes.sizeof(HighContrast), 0, None)
        user.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]
        user.SystemParametersInfoW.restype = wt.BOOL
        if not user.SystemParametersInfoW(0x42, contrast.cbSize, ctypes.byref(contrast), 0):
            return {'applied': False, 'reason': 'unavailable'}
        if contrast.dwFlags & 1:
            return {'applied': False, 'reason': 'high_contrast'}
        dwm = ctypes.WinDLL('dwmapi', use_last_error=True)
        setter = dwm.DwmSetWindowAttribute
        setter.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
        setter.restype = ctypes.c_long
        for attribute, color in PALETTE.items():
            value = wt.DWORD(color)
            if setter(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) != 0:
                reset_changed()
                return {'applied': False, 'reason': 'unavailable'}
            changed.append(attribute)
        return {'applied': True, 'reason': 'native_colors_requested'}
    except (OSError, AttributeError, ValueError):
        reset_changed()
        return {'applied': False, 'reason': 'unavailable'}
