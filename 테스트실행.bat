@echo off
rem Run all automated tests with the bundled Python (no network, no keys)
setlocal
cd /d "%~dp0"
"python\python.exe" -m unittest discover -s tests -t .
echo.
echo Exit code: %errorlevel%  (0 = all tests passed)
pause
