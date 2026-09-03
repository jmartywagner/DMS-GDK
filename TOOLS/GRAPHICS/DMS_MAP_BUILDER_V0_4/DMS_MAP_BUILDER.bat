@echo off
setlocal
cd /d "%~dp0"
title DMS Map Builder V0.5 ERGONOMIE
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 dms_map_builder.py
) else (
    python dms_map_builder.py
)
if errorlevel 1 (
    echo.
    echo DMS Map Builder s'est arrete avec une erreur.
    echo Lance CHECK_BUILD.bat puis envoie toute la console.
    pause
)
