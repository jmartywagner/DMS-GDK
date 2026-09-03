@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if "%~1"=="" (
  echo Glisse une ROM .dmc sur RUN_ROM.bat ou passe son chemin.
  pause
  exit /b 2
)
set "PY=python"
%PY% -c "import sys;raise SystemExit(0 if sys.version_info.major==3 else 1)" >nul 2>&1
if errorlevel 1 set "PY=py -3"
%PY% "GDK\tools\dms_run_rom.py" "%~f1"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
