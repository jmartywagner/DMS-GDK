@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\dms1_audio_asset_builder.ps1"
if errorlevel 1 pause
endlocal
