@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Lance le hub via Windows Script Host pour ne pas garder une console ouverte.
if exist "%~dp0GDK\launcher\dms_hidden_run.vbs" (
  wscript.exe //nologo "%~dp0GDK\launcher\dms_hidden_run.vbs" "%~dp0GDK\launcher\DMS_GDK.py"
  exit /b 0
)

rem Repli de secours uniquement si le helper manque.
set "PY=python"
%PY% -c "import sys;raise SystemExit(0 if sys.version_info.major==3 else 1)" >nul 2>&1
if errorlevel 1 set "PY=py -3"
%PY% "GDK\launcher\DMS_GDK.py"
if errorlevel 1 (
  echo.
  echo ERREUR : le hub DMS-GDK ne s'est pas lance.
  echo Lance ADMIN\DMS_DOCTOR.bat pour diagnostiquer l environnement.
  pause
)
