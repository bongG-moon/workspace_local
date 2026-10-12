"""Exercise real Windows Shell window properties without changing user pins."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CSC = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
PROBE = r'''
internal class Probe {
 [System.Runtime.InteropServices.DllImport("shell32.dll")]
 static extern int SHGetPropertyStoreForWindow(IntPtr hwnd, ref Guid iid, [System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.Interface)] out Store store);
 [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
 struct Key { public Guid fmt; public uint id; }
 [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Explicit, Size=24)]
 struct Value { [System.Runtime.InteropServices.FieldOffset(0)] public ushort type; [System.Runtime.InteropServices.FieldOffset(8)] public IntPtr ptr; [System.Runtime.InteropServices.FieldOffset(8)] public short flag; }
 [System.Runtime.InteropServices.ComImport, System.Runtime.InteropServices.Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), System.Runtime.InteropServices.InterfaceType(System.Runtime.InteropServices.ComInterfaceType.InterfaceIsIUnknown)]
 interface Store { void GetCount(out uint n); void GetAt(uint i,out Key k); void GetValue(ref Key k,out Value v); void SetValue(ref Key k,ref Value v); void Commit(); }
 [System.Runtime.InteropServices.DllImport("ole32.dll")] static extern int PropVariantClear(ref Value v);
 static void Check(Store store,uint id,string expected) {
  var k = new Key {fmt=new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),id=id}; Value v; store.GetValue(ref k,out v);
  try { string got=v.type==31?System.Runtime.InteropServices.Marshal.PtrToStringUni(v.ptr):(v.type==11?v.flag.ToString():""); if(got!=expected)throw new Exception("Property "+id+" did not match"); }
  finally { PropVariantClear(ref v); }
 }
 [STAThread] static void Main(string[] args) {
  var home=new Uri("http://127.0.0.1:53221/#token=private"); Uri target;
  foreach(string path in new [] {"/manual/guide", "/manual/guide#scheduling", "/manual/AX_WORKSPACE_STORY.html"})
   if(!TryManualTarget(home,"http://127.0.0.1:53221"+path,true,out target)||target.Fragment.Contains("token"))throw new Exception("Guide link blocked");
  foreach(string suffix in new [] {"/api/bootstrap", "/manual/guide?token=private", "/manual/guide#token=private", "/manual/../api/bootstrap", "/manual/AX_WORKSPACE_STORY.html?x=1"})
   if(TryManualTarget(home,"http://127.0.0.1:53221"+suffix,true,out target))throw new Exception("Unexpected guide target allowed");
  if(TryManualTarget(home,"http://127.0.0.1:53222/manual/guide",true,out target)||TryManualTarget(home,"http://127.0.0.1:53221/manual/guide",false,out target))throw new Exception("Origin/gesture guard missing");
  string exe=System.Reflection.Assembly.GetExecutingAssembly().Location;
  string cmd="\""+exe+"\" --state \"C:\\한글 폴더\\isolated\"";
  bool valid=args[0]=="valid";
  Environment.SetEnvironmentVariable("COMPANY_WORKSPACE_RELAUNCH_ICON", valid?exe:null);
  Environment.SetEnvironmentVariable("COMPANY_WORKSPACE_RELAUNCH_COMMAND", valid?cmd:null);
  WorkspaceTaskbar.SetProcessIdentity();
  using(var form=new System.Windows.Forms.Form()) {
   WorkspaceTaskbar.Apply(form.Handle);
   Store store; Guid iid=typeof(Store).GUID; System.Runtime.InteropServices.Marshal.ThrowExceptionForHR(SHGetPropertyStoreForWindow(form.Handle,ref iid,out store));
   try { Check(store,5,"AX.Workspace.Desktop"); if(valid){Check(store,2,cmd);Check(store,3,"\""+exe+"\",0");Check(store,4,"AX Workspace");}else{Check(store,2,"");Check(store,9,"-1");} }
   finally {System.Runtime.InteropServices.Marshal.ReleaseComObject(store);}
  }
  Console.WriteLine("Verified");
 }
}
'''
@unittest.skipUnless(os.name == 'nt' and CSC.is_file(), 'Windows Shell and .NET Framework compiler required')
class TaskbarIdentityTests(unittest.TestCase):
 def test_real_shell_properties_use_entry_exe_and_never_pin_pipe_only_host(self):
  source=(ROOT/'deploy/Workspace.Desktop.cs').read_text(encoding='utf-8-sig')
  helper=source.split('internal static class WorkspaceTaskbar',1)[1].split('internal static class WorkspaceAppearance',1)[0]
  manual = source[source.index('    internal static bool TryManualTarget'):source.index('    private void OpenManual')]
  with tempfile.TemporaryDirectory(prefix='ax-taskbar-') as tmp:
   path=Path(tmp); cs=path/'probe.cs'; exe=path/'probe.exe'
   cs.write_text('using System;using System.IO;using System.Text.RegularExpressions;\ninternal static class WorkspaceTaskbar'+helper+PROBE.replace('internal class Probe {', 'internal class Probe {' + manual),encoding='utf-8')
   result=subprocess.run([str(CSC),'/nologo','/platform:x64','/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll',f'/out:{exe}',str(cs)],capture_output=True,text=True,timeout=30)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
   for mode in ('valid','missing'):
    with self.subTest(mode=mode):
     result=subprocess.run([str(exe),mode],capture_output=True,text=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
     self.assertEqual(result.returncode,0,result.stdout+result.stderr)
     self.assertIn('Verified',result.stdout)
