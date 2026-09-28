param(
    [ValidateSet('folder', 'files')][string]$Kind,
    [long]$OwnerHandle = 0
)

function Initialize-WorkspacePicker {
    if ([Threading.Thread]::CurrentThread.ApartmentState -ne 'STA') {
        throw 'The path picker requires PowerShell -STA.'
    }
    Add-Type -AssemblyName System.Windows.Forms
    if (-not ('WorkspacePicker.NativePathDialog' -as [type])) {
        $sourcePath = Join-Path $PSScriptRoot 'WorkspacePicker.cs'
        Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing -TypeDefinition ([IO.File]::ReadAllText($sourcePath))
    }
    # Must precede visual styles and creation of every proxy/dialog HWND.
    [WorkspacePicker.DisplayScaling]::Initialize()
    [Windows.Forms.Application]::EnableVisualStyles()
    [Windows.Forms.Application]::SetCompatibleTextRenderingDefault($false)
}

function New-WorkspacePickerOwner([long]$Handle) {
    $external = [WorkspacePicker.NativeOwner]::FromHandle($Handle)
    $window = New-Object WorkspacePicker.OwnerForm
    $window.Text = 'Company Workspace'
    $window.FormBorderStyle = 'FixedToolWindow'
    $window.ControlBox = $false
    $window.StartPosition = 'CenterScreen'
    $window.ClientSize = New-Object Drawing.Size(380, 64)
    $window.ShowInTaskbar = ($null -eq $external)
    $icon = $null
    $iconPath = Join-Path $PSScriptRoot 'web\app-icon.ico'
    if (Test-Path -LiteralPath $iconPath -PathType Leaf) {
        $icon = New-Object Drawing.Icon($iconPath)
        $window.Icon = $icon
    }
    # A real, visible owner can be activated. Opacity=0 prevented reliable focus.
    $label = New-Object Windows.Forms.Label
    $label.Dock = 'Fill'
    $label.TextAlign = 'MiddleCenter'
    $label.Text = '선택 창에서 업무 폴더나 첨부할 파일을 골라 주세요.'
    $window.Controls.Add($label)
    if ($null -ne $external) {
        $bounds = $external.Bounds()
        if (-not $bounds.IsEmpty) {
            $area = [Windows.Forms.Screen]::FromHandle($external.Handle).WorkingArea
            $window.StartPosition = 'Manual'
            $x = [Math]::Max($area.Left, [Math]::Min($bounds.Left + ($bounds.Width - $window.Width) / 2, $area.Right - $window.Width))
            $y = [Math]::Max($area.Top, [Math]::Min($bounds.Top + ($bounds.Height - $window.Height) / 2, $area.Bottom - $window.Height))
            $window.Location = New-Object Drawing.Point([int]$x, [int]$y)
        }
    }
    return @{ Window = $window; External = $external; Icon = $icon }
}

function Set-WorkspacePickerForeground($Window) {
    [WorkspacePicker.NativeOwner]::TryActivate($Window.Handle)
}

function Show-WorkspacePickerOwner($Context) {
    $window = $Context.Window
    # Do not raise above an unrelated app that became active during startup.
    $mayActivate = ($null -eq $Context.External -or $Context.External.IsForeground())
    $window.ActivateWhenShown = $mayActivate
    # The proxy is owned by Workspace, but only this local proxy is disabled by
    # ShowDialog. A timeout/killed helper must never leave the real app disabled.
    if ($null -ne $Context.External) { $window.Show($Context.External) }
    else { $window.Show() }
    if (-not $mayActivate) { return }
    $window.TopMost = $true
    try {
        $window.BringToFront()
        $window.Activate()
        Set-WorkspacePickerForeground $window
    } finally {
        # A one-time raise, never an always-on-top window or focus-stealing loop.
        $window.TopMost = $false
    }
}

function New-WorkspacePathDialog([string]$Kind) {
    return New-Object WorkspacePicker.NativePathDialog($Kind)
}
function Invoke-WorkspacePathPicker {
    param([ValidateSet('folder', 'files')][string]$Kind, [long]$OwnerHandle = 0)
    Initialize-WorkspacePicker
    $context = $null
    $dialog = $null
    $paths = @()
    try {
        $context = New-WorkspacePickerOwner $OwnerHandle
        $dialog = New-WorkspacePathDialog $Kind
        Show-WorkspacePickerOwner $context
        if ($dialog.ShowDialog($context.Window) -eq 'OK') {
            if ($Kind -eq 'folder') { $paths = @($dialog.SelectedPath) }
            else { $paths = @($dialog.FileNames) }
        }
        ConvertTo-Json -InputObject @($paths) -Compress
    } finally {
        try {
            if ($null -ne $dialog) { $dialog.Dispose() }
        } finally {
            if ($null -ne $context) {
                try {
                    $context.Window.TopMost = $false
                    $context.Window.Close()
                } finally {
                    try { $context.Window.Dispose() }
                    finally { if ($null -ne $context.Icon) { $context.Icon.Dispose() } }
                }
            }
        }
    }
}

# Dot-sourcing loads the lifecycle functions for noninteractive regression tests.
if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    Invoke-WorkspacePathPicker -Kind $Kind -OwnerHandle $OwnerHandle
}
