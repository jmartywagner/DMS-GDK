@echo off
rem DMS Display Profiles V1.1 safety wrapper.
rem V1.0 rebuilt the whole PC runtime; V1.1 intentionally builds only the display host.
call "%~dp0BUILD_DISPLAY_HOST_ONLY.bat"
exit /b %errorlevel%
