@echo off
setlocal
cd /d "%~dp0"
title DMS Asset Lab V0.3 - Build Check
echo ============================================================
echo DMS ASSET LAB V0.3 - CHECK
echo ============================================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m py_compile dms_asset_lab.py
) else (
    python -m py_compile dms_asset_lab.py
)
if errorlevel 1 (
    echo [ECHEC] Syntaxe Python invalide.
    pause
    exit /b 1
)
echo [OK] Syntaxe Python valide.
echo [OK] Aucun package pip requis.
echo.
echo Lance ensuite DMS_ASSET_LAB.bat.
pause
