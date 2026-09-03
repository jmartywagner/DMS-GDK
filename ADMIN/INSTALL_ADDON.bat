@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
if "%~1"=="" (
 echo Glisse un ZIP add-on DMS sur ce fichier.
 echo Aucun ecrasement silencieux n'est autorise.
 pause
 exit /b 2
)
set "PY=python"
%PY% -c "import sys;raise SystemExit(0 if sys.version_info.major==3 else 1)" >nul 2>&1
if errorlevel 1 set "PY=py -3"
%PY% "GDK\tools\dms_addon_install.py" "%~f1"
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
