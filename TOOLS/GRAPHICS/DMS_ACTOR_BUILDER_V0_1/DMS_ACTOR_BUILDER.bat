@echo off
setlocal
cd /d "%~dp0"
title DMS Actor Builder V1.1 - BIG UX BUILD
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 dms_actor_builder.py
) else (
    python dms_actor_builder.py
)
if errorlevel 1 (
    echo.
    echo DMS Actor Builder s'est arrete avec une erreur.
    echo Lance CHECK_BUILD.bat puis envoie toute la console.
    pause
)
