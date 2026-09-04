@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Theta Council - setup

set "PY="
for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "%ProgramFiles%\Python313\python.exe"
  "%ProgramFiles%\Python312\python.exe"
) do if not defined PY if exist %%~P set "PY=%%~P"

if not defined PY (
  for /f "delims=" %%A in ('where python 2^>nul') do (
    echo %%A | find /i "WindowsApps" >nul || if not defined PY set "PY=%%A"
  )
)

if not defined PY (
  echo.
  echo   Python was not found.
  echo   Install it with:  winget install Python.Python.3.12
  echo   then run this file again.
  echo.
  pause
  exit /b 1
)

echo.
echo   Using %PY%
echo.
"%PY%" setup_keys.py
if errorlevel 1 (
  echo.
  echo   Setup did not complete. Nothing was saved. You can just run this again.
  echo.
  pause
  exit /b 1
)

echo.
echo   ============================================================
echo    Running preflight - checking the live Alpaca connection
echo   ============================================================
echo.
"%PY%" -m council preflight
echo.
echo   Copy everything above and paste it back into the chat.
echo.
pause
