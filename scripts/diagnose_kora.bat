@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
echo ================================================================
echo KORA - SYSTEM DIAGNOSTICS
echo ================================================================
if not exist "src\diagnose.py" (
  echo [ERROR] src\diagnose.py is missing. The project folder is incomplete.
  exit /b 10
)
if exist ".venv\Scripts\python.exe" (
  set "PYTHON=.venv\Scripts\python.exe"
) else (
  set "PYTHON=python"
  echo [WARN] .venv not found; using system Python.
)
if /i "%~1"=="--full" (
  echo [INFO] Full diagnostic may start KORA and temporarily use data\workspace\.
  "%PYTHON%" src\diagnose.py
) else if /i "%~1"=="--models-only" (
  "%PYTHON%" src\diagnose.py --models-only
) else (
  "%PYTHON%" src\diagnose.py --quick
)
set "RC=%ERRORLEVEL%"
echo.
echo ================================================================
echo Diagnostic exit code: %RC%
echo ================================================================
pause
exit /b %RC%
