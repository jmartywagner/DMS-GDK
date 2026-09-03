@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title DMR Converter

set "DMSPY="
python -c "import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)" >nul 2>&1
if not errorlevel 1 set "DMSPY=python"
if defined DMSPY goto :run
py -3 -c "import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)" >nul 2>&1
if not errorlevel 1 set "DMSPY=py -3"
if defined DMSPY goto :run

echo [ERREUR] Python 3 est introuvable.
pause
exit /b 1

:run
REM P0.3.1 DLLSAFE:
REM NE PAS reconstruire automatiquement dms1emu.exe.
REM Le GDK de l'utilisateur doit rester utilisable meme si MSYS2/g++ est incomplet.
%DMSPY% "%~dp0dms_furnace_dmr.py"
if errorlevel 1 (
  echo.
  echo Le convertisseur s'est ferme sur une erreur.
  pause
  exit /b 1
)
exit /b 0
