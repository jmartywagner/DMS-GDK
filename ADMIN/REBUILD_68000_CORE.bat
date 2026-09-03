@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "GDK\toolchain\install_full_68000_core.ps1"
echo.
pause
