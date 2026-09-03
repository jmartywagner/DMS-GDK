@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
set "PY=python"
%PY% -c "import sys;raise SystemExit(0 if sys.version_info.major==3 else 1)" >nul 2>&1
if errorlevel 1 set "PY=py -3"
echo ============================================================
echo DMS-GDK - TEST GLOBAL DE TOUS LES PROJETS GCC
echo ============================================================
echo Les projets seront BUILDES un par un, sans lancer les ROM.
echo Un seul rapport final sera genere.
echo.
%PY% "GDK\tools\dms_test_all_projects.py"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo PASS : TOUS LES PROJETS SE CONSTRUISENT.
) else (
  echo ECHEC : au moins un projet ne se construit pas.
  echo Consulte DOCS_REPORTS\current\TEST_ALL_PROJECTS_LAST.txt
)
echo.
pause
exit /b %RC%
