Option Explicit
Dim shell, fs, root, command, result, launcher, powershell, launchError
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
launcher = root & "\deploy\Start-CompanyWorkspace.ps1"
If Not fs.FileExists(launcher) Then
    MsgBox Korean("C2E4 D589 20 D30C C77C C774 20 C5C6 C2B5 B2C8 B2E4 2E 20 5A 49 50 20 C804 CCB4 B97C 20 C0C8 20 D3F4 B354 C5D0 20 C555 CD95 20 D574 C81C D55C 20 B4A4 20 B2E4 C2DC 20 C2E4 D589 D574 20 C8FC C138 C694 2E"), vbExclamation, "Company Workspace"
    WScript.Quit 41
End If
powershell = fs.GetSpecialFolder(0) & "\System32\WindowsPowerShell\v1.0\powershell.exe"
' Use the same Windows PowerShell profile/environment as an ordinary terminal.
' This process and its children only; Group Policy still takes precedence.
command = Chr(34) & powershell & Chr(34) & " -NoLogo -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & launcher & Chr(34)
If WScript.Arguments.Count > 0 Then
    If LCase(WScript.Arguments(0)) = "demo" Then command = command & " -Demo"
End If
On Error Resume Next
result = shell.Run(command, 0, True)
launchError = Err.Number
On Error GoTo 0
If launchError <> 0 Then
    MsgBox Korean("57 6F 72 6B 73 70 61 63 65 20 C2E4 D589 AE30 B97C 20 C2DC C791 D558 C9C0 20 BABB D588 C2B5 B2C8 B2E4 2E 20 C2E4 D589 20 D30C C77C 20 B610 B294 20 C870 C9C1 C758 20 C2A4 D06C B9BD D2B8 20 C815 CC45 C744 20 D655 C778 D574 20 C8FC C138 C694 2E 20 C624 B958 20 CF54 B4DC 3A 20") & CStr(launchError), vbExclamation, "Company Workspace"
    WScript.Quit 1
End If
' 20 means PowerShell already presented the specific Korean failure.
If result = 1 Then
    MsgBox Korean("57 6F 72 6B 73 70 61 63 65 20 C2DC C791 C774 20 C644 B8CC B418 C9C0 20 C54A C558 C2B5 B2C8 B2E4 2E 20 50 6F 77 65 72 53 68 65 6C 6C 20 C2E4 D589 20 C815 CC45 2C 20 C2A4 D06C B9BD D2B8 20 D574 C11D 20 B610 B294 20 C2DC C791 20 D504 B85C D544 20 C911 20 C5B4 B290 20 B2E8 ACC4 C5D0 C11C 20 C2E4 D328 D588 B294 C9C0 20 C544 C9C1 20 D655 C778 B418 C9C0 20 C54A C558 C2B5 B2C8 B2E4 2E A AC19 C740 20 D3F4 B354 C758 20 43 68 65 63 6B 2D 57 6F 72 6B 73 70 61 63 65 2E 63 6D 64 B97C 20 C2E4 D589 D574 20 C9C4 B2E8 20 ACB0 ACFC B97C 20 D655 C778 D574 20 C8FC C138 C694 2E 20 C624 B958 20 CF54 B4DC 3A 20") & CStr(result), vbExclamation, "Company Workspace"
ElseIf result <> 0 And result <> 20 Then
    MsgBox Korean("57 6F 72 6B 73 70 61 63 65 20 C2E4 D589 AE30 B97C 20 C2DC C791 D558 C9C0 20 BABB D588 C2B5 B2C8 B2E4 2E 20 C2E4 D589 20 D30C C77C 20 B610 B294 20 C870 C9C1 C758 20 C2A4 D06C B9BD D2B8 20 C815 CC45 C744 20 D655 C778 D574 20 C8FC C138 C694 2E 20 C624 B958 20 CF54 B4DC 3A 20") & CStr(result), vbExclamation, "Company Workspace"
End If
WScript.Quit result

' ASCII source keeps Korean messages intact under every WSH ANSI code page.
Function Korean(hexCodes)
    Dim item, text
    text = ""
    For Each item In Split(hexCodes, " ")
        text = text & ChrW(CLng("&H" & item))
    Next
    Korean = text
End Function
