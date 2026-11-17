@echo off
rem Job Hunter AI - double-click to install and start the setup wizard.
rem Runs scripts\setup.ps1 for this window only (no system settings are changed).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
echo.
pause
