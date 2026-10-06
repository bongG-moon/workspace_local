"""Read-only kernel TCP-owner and token checks for administrator app requests."""
from __future__ import annotations
import ctypes
from ctypes import wintypes as w
import socket


class _SidAttributes(ctypes.Structure):
    _fields_ = [('sid', ctypes.c_void_p), ('attributes', w.DWORD)]


class _TcpRow(ctypes.Structure):
    _fields_ = [(name, w.DWORD) for name in ('state','localAddress','localPort','remoteAddress','remotePort','pid')]


class WindowsPeer:
    def __init__(self):
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.adv=ctypes.WinDLL('advapi32',use_last_error=True)
        self.ip=ctypes.WinDLL('iphlpapi',use_last_error=True)
        bindings=[(self.kernel,'GetCurrentProcess',w.HANDLE,[]),
                  (self.kernel,'OpenProcess',w.HANDLE,[w.DWORD,w.BOOL,w.DWORD]),
                  (self.kernel,'CloseHandle',w.BOOL,[w.HANDLE]),
                  (self.kernel,'LocalFree',w.HANDLE,[ctypes.c_void_p]),
                  (self.adv,'OpenProcessToken',w.BOOL,[w.HANDLE,w.DWORD,ctypes.POINTER(w.HANDLE)]),
                  (self.adv,'GetTokenInformation',w.BOOL,[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]),
                  (self.adv,'EqualSid',w.BOOL,[ctypes.c_void_p,ctypes.c_void_p]),
                  (self.adv,'GetSidSubAuthorityCount',ctypes.POINTER(ctypes.c_ubyte),[ctypes.c_void_p]),
                  (self.adv,'GetSidSubAuthority',ctypes.POINTER(w.DWORD),[ctypes.c_void_p,w.DWORD]),
                  (self.adv,'ConvertStringSidToSidW',w.BOOL,[w.LPCWSTR,ctypes.POINTER(ctypes.c_void_p)]),
                  (self.adv,'DuplicateToken',w.BOOL,[w.HANDLE,ctypes.c_int,ctypes.POINTER(w.HANDLE)]),
                  (self.adv,'CheckTokenMembership',w.BOOL,[w.HANDLE,ctypes.c_void_p,ctypes.POINTER(w.BOOL)]),
                  (self.ip,'GetExtendedTcpTable',w.DWORD,[ctypes.c_void_p,ctypes.POINTER(w.DWORD),w.BOOL,w.DWORD,ctypes.c_int,w.DWORD])]
        for dll,name,result,args in bindings:
            fn=getattr(dll,name);fn.restype=result;fn.argtypes=args
        token=self._token(self.kernel.GetCurrentProcess())
        try:
            self.user=self._info(token,1)
            self.session=self._number(token,18)
        finally:self.kernel.CloseHandle(token)

    def _token(self, process):
        token=w.HANDLE()
        if not self.adv.OpenProcessToken(process,0x0008|0x0002,ctypes.byref(token)):raise ctypes.WinError(ctypes.get_last_error())
        return token

    def _info(self, token, kind):
        size=w.DWORD()
        self.adv.GetTokenInformation(token,kind,None,0,ctypes.byref(size))
        if not 0<size.value<=65536:raise OSError('Unverified token information')
        buffer=ctypes.create_string_buffer(size.value)
        if not self.adv.GetTokenInformation(token,kind,buffer,size,ctypes.byref(size)):raise ctypes.WinError(ctypes.get_last_error())
        return buffer

    def _number(self, token, kind):
        return w.DWORD.from_buffer(self._info(token,kind)).value

    def owner(self, local, remote):
        size=w.DWORD()
        self.ip.GetExtendedTcpTable(None,ctypes.byref(size),False,2,5,0)
        if not 4<=size.value<=16*1024*1024:raise OSError('Unverified TCP table')
        buffer=ctypes.create_string_buffer(size.value)
        if self.ip.GetExtendedTcpTable(buffer,ctypes.byref(size),False,2,5,0):raise OSError('Unverified TCP owner')
        count=w.DWORD.from_buffer(buffer).value
        if count>65536 or 4+count*ctypes.sizeof(_TcpRow)>size.value:raise OSError('Invalid TCP table')
        local_addr=int.from_bytes(socket.inet_aton(local[0]),'little')
        remote_addr=int.from_bytes(socket.inet_aton(remote[0]),'little')
        matches=set()
        for row in (_TcpRow*count).from_buffer(buffer,4):
            # Query the CLIENT row, not the server's accepted socket row.
            if (row.localAddress==remote_addr and row.remoteAddress==local_addr and
                socket.ntohs(row.localPort&0xffff)==remote[1] and socket.ntohs(row.remotePort&0xffff)==local[1]):
                matches.add(int(row.pid))
        if len(matches)!=1 or 0 in matches:raise OSError('Ambiguous TCP owner')
        return matches.pop()

    def flags(self, pid):
        process=self.kernel.OpenProcess(0x1000,False,pid)
        if not process:raise ctypes.WinError(ctypes.get_last_error())
        token=None;duplicate=w.HANDLE();admin_sid=ctypes.c_void_p()
        try:
            token=self._token(process)
            user=self._info(token,1)
            identity=bool(self.adv.EqualSid(_SidAttributes.from_buffer(self.user).sid,_SidAttributes.from_buffer(user).sid)) and self.session==self._number(token,18)
            label=self._info(token,25);sid=_SidAttributes.from_buffer(label).sid
            count=self.adv.GetSidSubAuthorityCount(sid).contents.value
            if not count:raise OSError('Invalid integrity SID')
            integrity=self.adv.GetSidSubAuthority(sid,count-1).contents.value
            administrator=w.BOOL(False)
            if not self.adv.ConvertStringSidToSidW('S-1-5-32-544',ctypes.byref(admin_sid)):raise OSError('Invalid admin SID')
            if not self.adv.DuplicateToken(token,2,ctypes.byref(duplicate)):raise OSError('Cannot query group membership')
            if not self.adv.CheckTokenMembership(duplicate,admin_sid,ctypes.byref(administrator)):raise OSError('Cannot query group membership')
            return {'identity':identity,'administrator':bool(administrator.value),
                    'elevated':bool(self._number(token,20)),'integrity':integrity}
        finally:
            if admin_sid.value:self.kernel.LocalFree(admin_sid)
            if duplicate:self.kernel.CloseHandle(duplicate)
            if token:self.kernel.CloseHandle(token)
            self.kernel.CloseHandle(process)

    def check(self, local, remote, *, identity_only=False):
        try:
            flags=self.flags(self.owner(local,remote))
            return flags['identity'] and (identity_only or flags['administrator'] and flags['elevated'] and flags['integrity']==0x3000)
        except (OSError,ValueError,AttributeError):return False
