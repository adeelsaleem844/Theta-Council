@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Theta Council - live paper trading

set "PY="
for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) do if not defined PY if exist %%~P set "PY=%%~P"
if not defined PY set "PY=python"

echo.
echo   Theta Council - trading the session on Alpaca paper.
echo   One cycle every 5 minutes. Close this window to stop.
echo.
"%PY%" -m council loop --interval 300
pause
