@echo off
setlocal
cd /d "%~dp0"
title DMS Asset Lab V0.3
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 dms_asset_lab.py
) else (
    python dms_asset_lab.py
)
if errorlevel 1 (
    echo.
    echo DMS Asset Lab s'est arrete avec une erreur.
    echo Lance CHECK_BUILD.bat puis envoie toute la console.
    pause
)
