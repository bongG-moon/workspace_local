@echo off
setlocal
title Company Workspace - Read-only check
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -File "%~dp0Check-Workspace.ps1"
if errorlevel 1 echo Diagnostic could not finish. Please share the displayed error. Do not change security policy.
echo.
pause
endlocal
