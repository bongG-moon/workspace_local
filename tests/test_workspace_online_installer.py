"""Compile/run the actual C# protocol with fake transport and harmless PE fixtures.

No network, NSIS install, user app changes, or production configuration override.
The native runner case executes only our generated, immediately-exiting test EXE.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CSC = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
RUNTIME = ROOT / 'deploy/Workspace.OnlineInstaller.cs'

HARNESS = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using AxWorkspace.OnlineInstaller;

public static class OnlineHarness
{
    static string Root, Fixture, Fixture2;
    static string Sha(byte[] bytes) { using (SHA256 h=SHA256.Create()) return BitConverter.ToString(h.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
    static byte[] Utf(string s) { return Encoding.UTF8.GetBytes(s); }
    static string Config = "{\"schema\":1,\"provider\":\"gitlab\",\"baseUrl\":\"https://gitlab.example.com/company\",\"projectId\":\"42\",\"packageName\":\"company-workspace\",\"channelPackage\":\"company-workspace-channel\",\"channelVersion\":\"0.0.0\",\"allowedDownloadOrigins\":[\"https://storage.example.com:9443\"]}";
    static Source Source() { return AxWorkspace.OnlineInstaller.Source.Parse(Utf(Config)); }
    static void Check(bool value, string label) { if (!value) throw new Exception(label); }
    static void Reject(Action action) { bool rejected=false; try { action(); } catch (Exception) { rejected=true; } Check(rejected,"should reject"); }
    sealed class Transport : ITransport {
        public Dictionary<string, Func<TransferResponse>> Responses = new Dictionary<string, Func<TransferResponse>>();
        public List<string> Calls = new List<string>();
        public TransferResponse Open(string url, CancellationToken token, DateTime deadline) { token.ThrowIfCancellationRequested(); Calls.Add(url); return Responses[url](); }
        public void Add(string url, byte[] bytes) { Responses[url]=delegate { return new TransferResponse { Status=200, ContentLength=bytes.Length, Body=new MemoryStream(bytes,false) }; }; }
    }
    sealed class Runner : IInstallerRunner {
        public int Calls, Exit;
        public string LastPath;
        public int Run(string filename, Action started) {
            Calls++; LastPath=filename; Check(Path.IsPathRooted(filename),"absolute launch");
            Reject(delegate { using(FileStream writer=new FileStream(filename,FileMode.Open,FileAccess.Write,FileShare.ReadWrite)) {} });
            Reject(delegate { File.Delete(filename); });
            Reject(delegate { Directory.Move(Path.GetDirectoryName(filename),Path.GetDirectoryName(filename)+"-moved"); });
            started(); return Exit;
        }
    }
    sealed class LimitedStream : Stream {
        long left; public int LargestRead; readonly CancellationTokenSource cancel;
        public LimitedStream(long size, CancellationTokenSource cancellation) { left=size;cancel=cancellation; }
        public override int Read(byte[] buffer,int offset,int count) { LargestRead=Math.Max(LargestRead,count); int read=(int)Math.Min(left,count); left-=read; Array.Clear(buffer,offset,read); if(cancel!=null)cancel.Cancel(); return read; }
        public override bool CanRead { get{return true;} } public override bool CanSeek { get{return false;} } public override bool CanWrite { get{return false;} }
        public override long Length { get{throw new NotSupportedException();} } public override long Position { get{throw new NotSupportedException();} set{throw new NotSupportedException();} }
        public override void Flush() {} public override long Seek(long a,SeekOrigin b){throw new NotSupportedException();}
        public override void SetLength(long a){throw new NotSupportedException();} public override void Write(byte[] a,int b,int c){throw new NotSupportedException();}
    }
    static string Manifest(string version,byte[] sums) {
        return "{\"schema\":1,\"channel\":\"stable\",\"version\":\""+version+"\",\"publishedAt\":\"2026-10-15T01:00:00Z\",\"title\":\"AX Workspace\",\"notes\":\"설치 안내\",\"files\":[{\"name\":\"Company-Workspace-"+version+"-vbs.zip\",\"size\":1,\"sha256\":\""+new string('a',64)+"\"},{\"name\":\"SHA256SUMS.txt\",\"size\":"+sums.Length+",\"sha256\":\""+Sha(sums)+"\"}]}";
    }
    sealed class Context {
        public Source source=Source(); public Transport transport=new Transport(); public Runner runner=new Runner();
        public Engine engine; public byte[] Setup, Sums, Metadata; public string Version, SetupUrl, SumsUrl;
        public Context(string version,string fixture) {
            Version=version; Setup=File.ReadAllBytes(fixture);
            Sums=Utf(Sha(Setup)+"  AX-Workspace-"+version+"-Setup.exe\n"); Metadata=Utf(Manifest(version,Sums));
            SetupUrl=source.AssetUrl(version,"AX-Workspace-"+version+"-Setup.exe"); SumsUrl=source.AssetUrl(version,"SHA256SUMS.txt");
            transport.Add(source.LatestUrl,Metadata);transport.Add(SumsUrl,Sums);transport.Add(SetupUrl,Setup);
            engine=new Engine(source,transport,runner,Path.Combine(Root,Guid.NewGuid().ToString("N")));
        }
        public Release Discover(){return engine.Discover(CancellationToken.None);}
        public int Install(){return engine.Install(Discover(),CancellationToken.None,null,null);}
        public void BadInstall(){Reject(delegate{Install();});Check(runner.Calls==0,"must not launch");}
        public void SetSums(byte[] bytes){Sums=bytes;transport.Add(SumsUrl,bytes);transport.Add(source.LatestUrl,Utf(Manifest(Version,bytes)));}
        public void SetSetup(byte[] bytes){Setup=bytes;transport.Add(SetupUrl,bytes);SetSums(Utf(Sha(bytes)+"  AX-Workspace-"+Version+"-Setup.exe\n"));}
    }
    public static int Main(string[] args) {
        try { Root=args[1];Fixture=args[2];Fixture2=args[3]; Run(args[0]); Console.WriteLine("PASS "+args[0]); return 0; }
        catch(Exception e) {Console.Error.WriteLine(e);return 1;}
    }
    static void Run(string test) {
        if(test=="valid_config") { Check(Source().LatestUrl=="https://gitlab.example.com/company/api/v4/projects/42/packages/generic/company-workspace-channel/0.0.0/latest.json","canonical URL"); return; }
        if(test.StartsWith("config_")) {
            string changed=Config;
            switch(test) {
                case "config_http":changed=changed.Replace("https://gitlab","http://gitlab");break;
                case "config_credentials":changed=changed.Replace("https://gitlab","https://user:secret@gitlab");break;
                case "config_encoded_dot":changed=changed.Replace("/company\"","/company/%2e%2e\"");break;
                case "config_raw_dot":changed=changed.Replace("/company\"","/company/../other\"");break;
                case "config_unknown_key":changed=changed.Replace("\"schema\":1","\"token\":\"secret\",\"schema\":1");break;
                case "config_duplicate":changed=changed.Replace("\"schema\":1","\"schema\":1,\"schema\":1");break;
                case "config_bool_schema":changed=changed.Replace("\"schema\":1","\"schema\":true");break;
                case "config_project_traversal":changed=changed.Replace("\"42\"","\"../43\"");break;
                case "config_origin_path":changed=changed.Replace("storage.example.com:9443","storage.example.com:9443/files");break;
                case "config_github":changed=changed.Replace("\"gitlab\"","\"github\"");break;
                case "config_oversize":Reject(delegate{AxWorkspace.OnlineInstaller.Source.Parse(new byte[16385]);});return;
            }
            Reject(delegate{AxWorkspace.OnlineInstaller.Source.Parse(Utf(changed));});return;
        }
        Context c=new Context("0.23.26",Fixture);
        switch(test) {
            case "success":Check(c.Install()==0,"success");Check(c.runner.Calls==1,"one launch");Check(!File.Exists(c.runner.LastPath),"cleanup");break;
            case "latest_changes":
                Check(c.Install()==0,"first latest");Context next=new Context("0.23.27",Fixture2);
                c.transport.Responses=next.transport.Responses;
                Check(c.Discover().Version=="0.23.27","same config follows new latest");Check(c.Install()==0,"new latest install");break;
            case "native_runner":
                Engine native=new Engine(c.source,c.transport,new NativeInstallerRunner(),Path.Combine(Root,"native"));
                bool started=false;Check(native.Install(native.Discover(CancellationToken.None),CancellationToken.None,null,delegate{started=true;})==0,"native exit");Check(started,"native started");break;
            case "nonzero_exit":c.runner.Exit=1;Check(c.Install()==1,"cancellation exit preserved");break;
            case "manifest_duplicate":c.transport.Add(c.source.LatestUrl,Utf(Manifest(c.Version,c.Sums).Replace("\"schema\":1","\"schema\":1,\"schema\":1")));c.BadInstall();break;
            case "manifest_malicious_version":c.transport.Add(c.source.LatestUrl,Utf(Manifest("../../payload",c.Sums)));c.BadInstall();break;
            case "manifest_malicious_name":c.transport.Add(c.source.LatestUrl,Utf(Manifest(c.Version,c.Sums).Replace("SHA256SUMS.txt","../SHA256SUMS.txt")));c.BadInstall();break;
            case "manifest_bool_size":c.transport.Add(c.source.LatestUrl,Utf(Manifest(c.Version,c.Sums).Replace("\"size\":1,","\"size\":true,")));c.BadInstall();break;
            case "manifest_oversize":c.transport.Add(c.source.LatestUrl,new byte[Engine.MaxMetadata+1]);c.BadInstall();break;
            case "checksum_size_mismatch":c.transport.Add(c.SumsUrl,Utf("bad"));c.BadInstall();break;
            case "checksum_hash_mismatch":byte[] bad=(byte[])c.Sums.Clone();bad[0]=(byte)'X';c.transport.Add(c.SumsUrl,bad);c.BadInstall();break;
            case "checksum_missing_setup":c.SetSums(Utf(new string('a',64)+"  Company-Workspace-0.23.26-vbs.zip\n"));c.BadInstall();break;
            case "checksum_duplicate_setup":c.SetSums(Utf(Encoding.UTF8.GetString(c.Sums)+Encoding.UTF8.GetString(c.Sums)));c.BadInstall();break;
            case "setup_hash_mismatch":byte[] mutated=(byte[])c.Setup.Clone();mutated[mutated.Length-1]^=1;c.transport.Add(c.SetupUrl,mutated);c.BadInstall();break;
            case "setup_wrong_version":c.SetSetup(File.ReadAllBytes(Fixture2));c.BadInstall();break;
            case "setup_not_pe":c.SetSetup(Utf("not an executable"));c.BadInstall();break;
            case "setup_fake_mz":c.SetSetup(Utf("MZnot an executable"));c.BadInstall();break;
            case "setup_missing":c.transport.Responses[c.SetupUrl]=delegate{return new TransferResponse{Status=404};};c.BadInstall();break;
            case "setup_encoded":c.transport.Responses[c.SetupUrl]=delegate{return new TransferResponse{Status=200,Encoding="gzip",Body=new MemoryStream(c.Setup)};};c.BadInstall();break;
            case "setup_declared_mismatch":c.transport.Responses[c.SetupUrl]=delegate{return new TransferResponse{Status=200,ContentLength=c.Setup.Length+1,Body=new MemoryStream(c.Setup)};};c.BadInstall();break;
            case "setup_large_declared":c.transport.Responses[c.SetupUrl]=delegate{return new TransferResponse{Status=200,ContentLength=Engine.MaxInstaller+1,Body=new MemoryStream()};};c.BadInstall();break;
            case "setup_large_stream":
                LimitedStream huge=new LimitedStream(Engine.MaxInstaller+1,null);c.transport.Responses[c.SetupUrl]=delegate{return new TransferResponse{Status=200,Body=huge};};
                c.BadInstall();Check(huge.LargestRead<=65536,"stream bound");break;
            case "cancel_before":
                CancellationTokenSource early=new CancellationTokenSource();early.Cancel();Reject(delegate{c.engine.Discover(early.Token);});Check(c.transport.Calls.Count==0,"no network");break;
            case "cancel_download":
                CancellationTokenSource during=new CancellationTokenSource();LimitedStream cancelStream=new LimitedStream(100000,during);
                c.transport.Responses[c.SetupUrl]=delegate{return new TransferResponse{Status=200,Body=cancelStream};};
                Release selected=c.Discover();Reject(delegate{c.engine.Install(selected,during.Token,null,null);});Check(c.runner.Calls==0,"cancel no launch");break;
            case "cancel_after_download":
                CancellationTokenSource after=new CancellationTokenSource();Release candidate=c.Discover();
                Reject(delegate{c.engine.Install(candidate,after.Token,delegate(long a,long b){after.Cancel();},null);});Check(c.runner.Calls==0,"cancel after download no launch");break;
            case "redirect_allowed":
                string allowed="https://storage.example.com:9443/setup?temporary=1";
                c.transport.Responses[c.SetupUrl]=delegate{return new TransferResponse{Status=302,Location=allowed};};c.transport.Add(allowed,c.Setup);Check(c.Install()==0,"allowed CDN");break;
            case "redirect_http":case "redirect_foreign":case "redirect_same_host":case "redirect_backslash":case "redirect_credentials":
                string target=test=="redirect_http"?"http://storage.example.com:9443/setup":test=="redirect_foreign"?"https://evil.example.com/setup":
                    test=="redirect_same_host"?"https://gitlab.example.com/other":test=="redirect_backslash"?"https://storage.example.com:9443/\\setup":"https://user:secret@storage.example.com:9443/setup";
                c.transport.Responses[c.SetupUrl]=delegate{return new TransferResponse{Status=302,Location=target};};c.BadInstall();Check(!c.transport.Calls.Contains(target),"rejected before network");break;
            case "redirect_loop":c.transport.Responses[c.SetupUrl]=delegate{return new TransferResponse{Status=302,Location=c.SetupUrl};};c.BadInstall();Check(c.transport.Calls.Count<=8,"bounded redirects");break;
            case "expired_deadline":Reject(delegate{Engine.Active(CancellationToken.None,DateTime.UtcNow.AddSeconds(-1));});break;
            case "unknown_file_preserved":
                string retained=null;c.engine.Install(c.Discover(),CancellationToken.None,null,delegate{retained=Path.Combine(Path.GetDirectoryName(c.runner.LastPath),"unrelated.txt");File.WriteAllText(retained,"keep");});
                Check(File.ReadAllText(retained)=="keep","only own file cleanup");File.Delete(retained);Directory.Delete(Path.GetDirectoryName(retained));break;
            default:throw new Exception("unknown test");
        }
    }
}
'''


@unittest.skipUnless(os.name == 'nt' and CSC.is_file(), 'Windows .NET Framework compiler required')
class OnlineInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = Path(tempfile.mkdtemp(prefix='online-installer-test-', dir=ROOT / 'build'))
        cls.harness = cls.temp / 'Harness.cs'
        cls.harness.write_text(HARNESS, encoding='utf-8-sig')
        cls.exe = cls.temp / 'Harness.exe'
        cls.compile('/target:exe', '/main:OnlineHarness', f'/out:{cls.exe}',
                    '/reference:System.Core.dll', '/reference:System.Windows.Forms.dll',
                    '/reference:System.Drawing.dll', str(RUNTIME), str(cls.harness))
        cls.fixtures = []
        for version in ('0.23.26', '0.23.27'):
            source = cls.temp / f'fixture-{version}.cs'
            source.write_text(
                f'[assembly:System.Reflection.AssemblyFileVersion("{version}.0")]\n'
                f'[assembly:System.Reflection.AssemblyInformationalVersion("{version}")]\n'
                '[assembly:System.Reflection.AssemblyProduct("AX Workspace")]\n'
                'class Fixture { static int Main() { return 0; } }', encoding='utf-8')
            exe = source.with_suffix('.exe')
            cls.compile('/target:winexe', f'/out:{exe}', str(source))
            cls.fixtures.append(exe)

    @classmethod
    def compile(cls, *args):
        result = subprocess.run([str(CSC), '/nologo', *args], capture_output=True, timeout=45)
        if result.returncode:
            raise AssertionError(result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp)

    def scenario(self, name):
        result = subprocess.run([str(self.exe), name, str(self.temp / name), *(str(x) for x in self.fixtures)],
                                capture_output=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))


for _name in (
    'valid_config', 'config_http', 'config_credentials', 'config_encoded_dot', 'config_raw_dot',
    'config_unknown_key', 'config_duplicate', 'config_bool_schema', 'config_project_traversal',
    'config_origin_path', 'config_github', 'config_oversize', 'success', 'latest_changes',
    'native_runner', 'nonzero_exit', 'manifest_duplicate', 'manifest_malicious_version',
    'manifest_malicious_name', 'manifest_bool_size', 'manifest_oversize', 'checksum_size_mismatch',
    'checksum_hash_mismatch', 'checksum_missing_setup', 'checksum_duplicate_setup', 'setup_hash_mismatch',
    'setup_wrong_version', 'setup_not_pe', 'setup_fake_mz', 'setup_missing', 'setup_encoded',
    'setup_declared_mismatch', 'setup_large_declared', 'setup_large_stream', 'cancel_before',
    'cancel_download', 'cancel_after_download', 'redirect_allowed', 'redirect_http', 'redirect_foreign',
    'redirect_same_host', 'redirect_backslash', 'redirect_credentials', 'redirect_loop', 'expired_deadline',
    'unknown_file_preserved',
):
    setattr(OnlineInstallerTests, 'test_' + _name, lambda self, name=_name: self.scenario(name))

if __name__ == '__main__':
    unittest.main()
