@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PY=python"
%PY% -c "import sys;raise SystemExit(0 if sys.version_info.major==3 else 1)" >nul 2>&1
if errorlevel 1 set "PY=py -3"
if "%~1"=="" (
  %PY% "dms_metadata_editor.py"
) else (
  %PY% "dms_metadata_editor.py" "%~f1"
)
exit /b %ERRORLEVEL%
