@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
title DMS-GDK - Display Host V1.1

echo ==============================================
echo   DMS DISPLAY HOST V1.1 - HOST SEULEMENT
echo ==============================================
echo.
echo Aucun runtime complet ne sera reconstruit.
echo Aucun DLL ne sera telecharge ou installe.
echo.
set "PY=python"
%PY% -c "import sys;raise SystemExit(0 if sys.version_info.major==3 else 1)" >nul 2>&1
if errorlevel 1 set "PY=py -3"

%PY% "RUNTIME\tools\build_display_host_windows.py"
if errorlevel 1 goto :fail

echo.
echo PASS - CRT / Scanlines / Composite actifs.
echo F11 = changement de profil a la volee.
pause
exit /b 0

:fail
echo.
echo STOP propre : rien d'autre dans le GDK n'a ete reconstruit.
echo Le runtime RAW existant reste compatible et utilisable.
pause
exit /b 2
