@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
echo DMS-GDK - REPARATION TOOLCHAIN 68000
if exist "TOOLCHAIN\m68k-elf" (
  if not exist "ARCHIVE\TOOLCHAIN_BACKUPS" mkdir "ARCHIVE\TOOLCHAIN_BACKUPS"
  for /f "tokens=1-4 delims=/ " %%a in ("%date%") do set DS=%%d%%c%%b
  set TS=%time::=%
  set TS=%TS: =0%
  move "TOOLCHAIN\m68k-elf" "ARCHIVE\TOOLCHAIN_BACKUPS\m68k-elf_%DS%_%TS:.=%" >nul
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "GDK\toolchain\install_m68k_toolchain.ps1"
echo.
pause
