@echo off
setlocal
cd /d "%~dp0\.."
python "GDK\tests\canonical_contract_test.py" || goto :fail
python "GDK\tests\public_release_smoke_test.py" || goto :fail
python "SAMPLES\07_PLATFORM_DEMO\tools\validate_platform_demo.py" || goto :fail
echo PASS
pause
exit /b 0
:fail
echo ECHEC
pause
exit /b 2
