@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo DMS ACTOR BUILDER V0.1 - CHECK
echo ============================================================
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m py_compile dms_actor_builder.py
) else (
    python -m py_compile dms_actor_builder.py
)
if errorlevel 1 (
    echo [ECHEC] Syntaxe Python invalide.
    pause
    exit /b 1
)
echo [OK] Syntaxe Python valide.
echo [OK] Aucune dependance pip.
pause
