@echo off
cd /d "%~dp0\..\..\.."
py -3 GDK\tests\dflow_contract_test.py
pause
