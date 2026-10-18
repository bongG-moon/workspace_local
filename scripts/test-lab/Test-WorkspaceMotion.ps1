[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$hostRoot = Join-Path $repoRoot 'build/desktop-host'
if (-not (Test-Path -LiteralPath (Join-Path $hostRoot 'Microsoft.Web.WebView2.Core.dll'))) {
    throw 'Build the pinned desktop host with deploy/New-WorkspaceDesktop.ps1 first.'
}
$output = Join-Path $repoRoot ('build/motion-check-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $output | Out-Null
foreach ($name in @('Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll')) {
    Copy-Item -LiteralPath (Join-Path $hostRoot $name) -Destination $output
}
function File-Uri([string]$Relative) { return ([Uri](Join-Path $repoRoot $Relative)).AbsoluteUri }
$styles = @('app.css','appearance.css') | ForEach-Object { '<link rel="stylesheet" href="' + (File-Uri "local_app/web/$_") + '">' }
$messages = 1..1000 | ForEach-Object { "<article class='message'><div class='message-body'><p>Workspace message $_ — repeated navigation rendering check.</p></div></article>" }
$fields = 1..80 | ForEach-Object { "<p>Settings item $_</p>" }
$html = '<meta charset="utf-8">' + ($styles -join '') + @'
<style>body{display:grid;grid-template-columns:1fr 292px;height:100vh;overflow:hidden}#task-view{height:100vh;overflow:auto}aside{overflow:auto}dialog{max-height:80vh}#dialog-editor{width:100%}</style>
'@
$html += '<div id="task-view" data-motion-step="a">' + ($messages -join '') + '</div><aside><section id="source-panel">Materials</section><section id="results-panel" hidden>Results</section></aside>'
$html += '<dialog id="motion-dialog"><h2>Workspace settings</h2><input id="dialog-editor" value="Input check">' + ($fields -join '') + '</dialog>'
$html += '<script src="' + (File-Uri 'scripts/test-lab/test-workspace-motion.js') + '"></script>'
$fixture = Join-Path $output 'fixture.html'
[IO.File]::WriteAllText($fixture, $html, (New-Object Text.UTF8Encoding($false)))
$exe = Join-Path $output 'MotionCheck.exe'
$arguments = @('/nologo','/target:exe','/platform:x64','/optimize+','/codepage:65001',('/out:'+$exe),
    '/reference:System.dll','/reference:System.Core.dll','/reference:System.Drawing.dll',
    '/reference:System.Windows.Forms.dll','/reference:System.Web.Extensions.dll',
    ('/reference:'+(Join-Path $hostRoot 'Microsoft.Web.WebView2.Core.dll')),
    ('/reference:'+(Join-Path $hostRoot 'Microsoft.Web.WebView2.WinForms.dll')),
    (Join-Path $PSScriptRoot 'test-workspace-motion.cs'))
& (Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe') @arguments
if ($LASTEXITCODE -ne 0) { throw 'Motion test compile failed.' }
$report = Join-Path $output 'report.json'
& $exe $fixture (Join-Path $output 'isolated-profile') $report > (Join-Path $output 'test.log')
if ($LASTEXITCODE -ne 0) { throw "Motion check failed. See $report" }
Write-Output "Motion benchmark completed. Review timings and memory comparison: $report"
