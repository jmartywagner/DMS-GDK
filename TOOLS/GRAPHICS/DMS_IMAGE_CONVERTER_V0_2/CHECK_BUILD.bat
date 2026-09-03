@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo DMS IMAGE CONVERTER V0.2 - CHECK
echo ============================================================
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m py_compile dms_image_converter.py
) else (
    python -m py_compile dms_image_converter.py
)
if errorlevel 1 (
    echo [ECHEC] Syntaxe Python invalide.
    pause
    exit /b 1
)
echo [OK] Syntaxe Python valide.
echo [OK] Aucune API.
echo [OK] PNG natif. JPG/BMP/WebP si Pillow est disponible.
pause
