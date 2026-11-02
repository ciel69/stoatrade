@echo off
rem Windows (cmd) entry point: tb <group> <command> [args]   (list: tb help)
rem Thin wrapper over src\tb\launcher.py: the Bybit key file and proxy removal are done there, same as ./tb on macOS.
rem If args contain cmd special characters (& | ^ %%), use tb.ps1 (PowerShell) instead.
rem Interpreter: TB_PYTHON (full path to python.exe), else .venv\Scripts\python.exe, else "py -3", else python; only Python 3.9+ counts.
rem The WindowsApps python.exe stub (opens the Store) fails the version probe and is skipped.
rem No usable Python: "tb setup" runs scripts\bootstrap.ps1 (offers to install it); other commands suggest "tb setup" (exit code 3).
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%" || exit /b 1
if defined PYTHONPATH (set "PYTHONPATH=%ROOT%\src;%PYTHONPATH%") else (set "PYTHONPATH=%ROOT%\src")
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "TB_PY="
if defined TB_PYTHON call :try "%TB_PYTHON%"
if not defined TB_PY if exist "%ROOT%\.venv\Scripts\python.exe" call :try "%ROOT%\.venv\Scripts\python.exe"
if not defined TB_PY call :try py -3
if not defined TB_PY call :try python
if not defined TB_PY goto nopython
%TB_PY% -m tb.launcher %*
exit /b %ERRORLEVEL%

:try
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if not errorlevel 1 set "TB_PY=%*"
exit /b 0

:nopython
if /i "%~1"=="setup" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\bootstrap.ps1" %*
  exit /b %ERRORLEVEL%
)
echo Python 3.9 or newer was not found (needed by tb). 1>&2
echo Run: tb.cmd setup   (checks the environment and offers to install Python; nothing is installed without your consent) 1>&2
echo Python installed elsewhere: set TB_PYTHON=C:\path\to\python.exe 1>&2
exit /b 3
