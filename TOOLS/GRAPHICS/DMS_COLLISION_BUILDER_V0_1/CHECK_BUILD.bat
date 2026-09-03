@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo DMS COLLISION BUILDER V0.1 - CHECK
echo ============================================================
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m py_compile dms_collision_builder.py
) else (
    python -m py_compile dms_collision_builder.py
)
if errorlevel 1 (
    echo [ECHEC] Syntaxe Python invalide.
    pause
    exit /b 1
)
echo [OK] Syntaxe Python valide.
echo [OK] Aucune dependance pip.
pause
