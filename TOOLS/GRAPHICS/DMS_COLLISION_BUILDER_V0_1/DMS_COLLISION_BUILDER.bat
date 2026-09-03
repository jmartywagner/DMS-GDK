@echo off
setlocal
cd /d "%~dp0"
title DMS Collision Builder V0.2.1 LARGE MAP
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 dms_collision_builder.py
) else (
    python dms_collision_builder.py
)
if errorlevel 1 (
    echo.
    echo DMS Collision Builder s'est arrete avec une erreur.
    echo Lance CHECK_BUILD.bat puis envoie toute la console.
    pause
)
