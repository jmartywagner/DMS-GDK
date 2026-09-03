@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "SCRIPT=%~dp0dms_game_flow_builder.py"
set "LOG=%~dp0DMS_GAME_FLOW_BUILDER_LAST_LAUNCH.log"
set "PYEXE="

> "%LOG%" echo DMS GAME FLOW BUILDER V0.1 - JOURNAL DE LANCEMENT
>>"%LOG%" echo Date: %DATE% %TIME%
>>"%LOG%" echo Dossier: %CD%

rem Priorite au Python 3.14 utilise par le GDK de reference.
if exist "C:\Python314\python.exe" set "PYEXE=C:\Python314\python.exe"

rem Sinon utiliser le launcher Python Windows.
if not defined PYEXE (
    for /f "usebackq delims=" %%P in (`py -3 -c "import sys; print(sys.executable)" 2^>nul`) do set "PYEXE=%%P"
)

rem Dernier repli : python.exe accessible dans le PATH.
if not defined PYEXE (
    for /f "usebackq delims=" %%P in (`python -c "import sys; print(sys.executable)" 2^>nul`) do set "PYEXE=%%P"
)

if not defined PYEXE goto :NO_PYTHON
if not exist "%SCRIPT%" goto :NO_SCRIPT

>>"%LOG%" echo Python: %PYEXE%
>>"%LOG%" "%PYEXE%" --version
>>"%LOG%" echo.

"%PYEXE%" "%SCRIPT%" %* >>"%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" exit /b 0

echo.
echo ============================================================
echo  DMS GAME FLOW BUILDER - ERREUR DE LANCEMENT
ECHO ============================================================
echo Le Builder n'a pas pu demarrer. Code erreur : %RC%
echo.
echo Journal :
echo %LOG%
echo.
type "%LOG%"
echo.
pause
exit /b %RC%

:NO_PYTHON
>>"%LOG%" echo ERREUR: aucun Python 3 detecte.
echo ERREUR : Python 3 introuvable.
echo Le GDK de reference utilise C:\Python314\python.exe.
echo Voir : %LOG%
pause
exit /b 9009

:NO_SCRIPT
>>"%LOG%" echo ERREUR: script introuvable: %SCRIPT%
echo ERREUR : dms_game_flow_builder.py est introuvable.
echo Verifie que le ZIP a ete extrait en entier.
echo Voir : %LOG%
pause
exit /b 2
