@echo off
setlocal
title Company Workspace - Python diagnostic
echo Checking Python in the current and Workspace launch contexts...
echo This may take about 1 minute. No installation or settings changes.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -ExecutionPolicy Bypass -File "%~dp0Check-Python.ps1"
if errorlevel 1 echo Diagnostic could not finish. Please share the displayed status with the maintainer.
echo.
pause
endlocal
