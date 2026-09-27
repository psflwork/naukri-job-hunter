@echo off
rem Double-click this file to start Naukri Job Hunter on Windows.
setlocal
cd /d "%~dp0"
title Naukri Job Hunter
set PYTHONUTF8=1
set "VPY=.venv\Scripts\python.exe"

if exist "%VPY%" goto :deps

set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=python"
if defined PY goto :venv

echo.
echo Python 3.10 or newer is needed - it's free.
echo   1. Download it from https://www.python.org/downloads/  - the big yellow button
echo   2. Open the downloaded file. IMPORTANT: tick "Add python.exe to PATH", then click Install Now
echo   3. Double-click START-HERE-Windows again
start "" "https://www.python.org/downloads/"
goto :end

:venv
echo Setting things up for the first time. This takes a few minutes...
%PY% -m venv .venv || goto :fail

:deps
fc /b requirements.txt .venv\.installed-requirements >nul 2>&1 && goto :browser
echo Installing dependencies...
"%VPY%" -m pip install -q --upgrade pip
"%VPY%" -m pip install -q -r requirements.txt || goto :fail
copy /y requirements.txt .venv\.installed-requirements >nul

:browser
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" goto :setup
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" goto :setup
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" goto :setup
if exist ".venv\.chromium-installed" goto :setup
echo Google Chrome not found - installing a built-in browser instead...
echo (Installing Google Chrome from https://www.google.com/chrome/ is recommended.)
"%VPY%" -m playwright install chromium || goto :fail
type nul > .venv\.chromium-installed

:setup
if "%~1"=="" goto :web
if exist config.yaml goto :start
"%VPY%" hunt.py setup || goto :fail

:start
"%VPY%" hunt.py %*
goto :end

:web
rem The web app has its own first-run steps in the browser.
"%VPY%" hunt.py web
goto :end

:fail
echo.
echo Something went wrong above. Take a screenshot of this window if you need help.

:end
echo.
pause
