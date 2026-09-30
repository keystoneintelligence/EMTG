@echo off
setlocal
rem Let Windows PowerShell construct its own module path, even when called by pwsh.
set "PSModulePath="
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\qualify-windows.ps1" %*
exit /b %ERRORLEVEL%
