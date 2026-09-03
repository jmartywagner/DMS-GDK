@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>&1
if not errorlevel 1 (
  python dms_scene_builder.py
  exit /b %errorlevel%
)
where py >nul 2>&1
if not errorlevel 1 (
  py -3 dms_scene_builder.py
  exit /b %errorlevel%
)
echo Python 3 introuvable.
pause
exit /b 2
