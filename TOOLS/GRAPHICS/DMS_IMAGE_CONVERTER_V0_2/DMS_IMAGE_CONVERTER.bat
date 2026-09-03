@echo off
setlocal
cd /d "%~dp0"
title DMS Image Converter V0.2
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 dms_image_converter.py
) else (
    python dms_image_converter.py
)
if errorlevel 1 (
    echo.
    echo DMS Image Converter s'est arrete avec une erreur.
    echo Lance CHECK_BUILD.bat puis envoie toute la console.
    pause
)
