@echo off
rem Armory uninstaller: double-click this file. It runs _installer\armory_install.ps1 (PowerShell),
rem which finds your Borderlands 2 folder through Steam and removes this package's files.
setlocal
rem a PowerShell 7 window's module path breaks Windows PowerShell 5.1: use its defaults
set "PSModulePath="
title Armory uninstaller
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_installer\armory_install.ps1" -Action uninstall -Source "%~dp0." %*
set CODE=%ERRORLEVEL%
endlocal & exit /b %CODE%
