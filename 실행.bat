@echo off
rem Jeju commercial-area diagnosis agent launcher (no install needed)
setlocal
cd /d "%~dp0"
if not exist "python\python.exe" (
  echo [ERROR] python\python.exe not found. Extract the whole ZIP first, then run this file.
  pause
  exit /b 1
)
set OPEN_BROWSER=true
"python\python.exe" -m jeju_agent %*
if errorlevel 1 pause
