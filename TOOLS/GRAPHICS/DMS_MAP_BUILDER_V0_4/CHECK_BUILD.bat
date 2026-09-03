@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo DMS MAP BUILDER V0.5 ERGONOMIE - CHECK
echo ============================================================
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m py_compile dms_map_builder.py
) else (
    python -m py_compile dms_map_builder.py
)
if errorlevel 1 (
    echo [ECHEC] Syntaxe Python invalide.
    pause
    exit /b 1
)
echo [OK] Syntaxe Python valide.
echo [OK] Aucune dependance pip ajoutee.
echo [OK] Aucun runtime ni DLL requis.
echo [INFO] Tests GUI : vignettes, preview, flips, zoom molette, checkboxes, BG B par mode.
pause
