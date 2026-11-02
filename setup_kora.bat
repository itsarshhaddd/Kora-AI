@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "DEPS_ONLY=0"
set "ASSETS_ONLY=0"
set "NO_DOWNLOAD=0"
set "ASSET_WARN=0"
for %%A in (%*) do (
  if /i "%%~A"=="--deps-only" set "DEPS_ONLY=1"
  if /i "%%~A"=="--assets-only" set "ASSETS_ONLY=1"
  if /i "%%~A"=="--no-download" set "NO_DOWNLOAD=1"
)

rem --assets-only: only download the missing AI models and llama.cpp runtime, then stop.
if "%ASSETS_ONLY%"=="1" goto :assets_only

 echo ================================================================
 echo KORA - WINDOWS SETUP
 echo ================================================================
echo [INFO] Project: %CD%

set "PYEXE="
where py >nul 2>&1
if not errorlevel 1 for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
if not defined PYEXE (
  where python >nul 2>&1
  if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
  echo [ERROR] Python 3.11 or newer, 64-bit, is required.
  echo         Install it from https://www.python.org/downloads/windows/ and retry.
  exit /b 1
)
"%PYEXE%" -c "import sys,platform; print('[INFO] Selected Python',sys.version.split()[0],platform.architecture()[0]); raise SystemExit(0 if sys.version_info >= (3,11) and platform.architecture()[0]=='64bit' else 1)"
if errorlevel 1 (
  echo [ERROR] Python 3.11+ 64-bit is required.
  exit /b 15
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/7] Creating virtual environment...
  "%PYEXE%" -m venv .venv
  if errorlevel 1 goto :venv_error
) else echo [1/7] Reusing existing .venv

".venv\Scripts\python.exe" -c "import sys,platform; print('[INFO] Environment Python',sys.version.split()[0],platform.architecture()[0]); raise SystemExit(0 if sys.version_info >= (3,11) and platform.architecture()[0]=='64bit' else 1)"
if errorlevel 1 (
  echo [ERROR] Existing .venv is not a compatible 64-bit Python 3.11+ environment.
  echo         Delete .venv and rerun setup with 64-bit Python 3.11 or newer.
  exit /b 16
)

if not exist "requirements.txt" (
  echo [ERROR] requirements.txt is missing.
  exit /b 4
)

echo [2/7] Updating pip build tools...
".venv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :pip_error

echo [3/7] Installing KORA dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :deps_error

rem Confirm the native PDF wheel is loadable in this Python environment.
echo [4/7] Checking native PDF dependency...
".venv\Scripts\python.exe" -c "import pymupdf"
if errorlevel 1 (
  echo [WARN] PyMuPDF failed to import. Reinstalling its wheel...
  ".venv\Scripts\python.exe" -m pip uninstall -y pymupdf >nul 2>&1
  ".venv\Scripts\python.exe" -m pip install --no-cache-dir --force-reinstall pymupdf
  if errorlevel 1 goto :pymupdf_error
  ".venv\Scripts\python.exe" -c "import pymupdf"
  if errorlevel 1 goto :pymupdf_error
)

for %%D in (data data\workspace data\models data\runtime data\runtime\vulkan data\runtime\cuda data\runtime\sycl data\runtime\cpu data\logs data\runtime\webview_storage) do if not exist "%%D" mkdir "%%D"
if "%DEPS_ONLY%"=="1" goto :deps_only

if "%NO_DOWNLOAD%"=="1" (
  echo [5/7] Skipping model and runtime download, --no-download was given.
) else (
  echo [5/7] Checking AI models, runtime, Tesseract OCR and LibreOffice, installing what is missing...
  call :download_assets
  if errorlevel 1 (
    set "ASSET_WARN=1"
    echo [WARN] Some AI files could not be downloaded. Check your internet connection, then run setup_kora.bat again.
  )
)

rem Make freshly installed Tesseract and LibreOffice visible to this window.
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "PATH=%ProgramFiles%\Tesseract-OCR;%PATH%"
if exist "%ProgramFiles%\LibreOffice\program\soffice.exe" set "PATH=%ProgramFiles%\LibreOffice\program;%PATH%"

echo [6/7] Compiling Python modules...
".venv\Scripts\python.exe" -m compileall -q src
if errorlevel 1 goto :compile_error

echo [7/7] Running safe preflight diagnostics...
".venv\Scripts\python.exe" src\diagnose.py --quick
set "DIAG_RC=%ERRORLEVEL%"
if not "%DIAG_RC%"=="0" (
  echo [WARN] Preflight diagnostics reported issues. Review the output above.
  exit /b %DIAG_RC%
)

echo.
echo [SUCCESS] KORA dependencies and source checks are ready.
if "%ASSET_WARN%"=="1" (
  echo [INFO] The AI models or runtime are still missing. Run setup_kora.bat again with internet access,
  echo        or place the files manually in data\models\ and data\runtime\vulkan\.
) else (
  echo [INFO] AI models and the llama.cpp runtime are ready.
)
echo [INFO] Launch KORA with run_kora.bat.
exit /b 0

:deps_only
echo [SUCCESS] Python dependencies installed. Model and runtime checks skipped by --deps-only.
exit /b 0
:venv_error
echo [ERROR] Could not create the virtual environment.
exit /b 2
:pip_error
echo [ERROR] pip build-tool installation failed.
exit /b 3
:deps_error
echo [ERROR] Dependencies could not be installed. Check internet access and requirements.txt.
exit /b 5
:pymupdf_error
echo [ERROR] PyMuPDF still cannot be imported after reinstall.
echo         Delete .venv and rerun setup using 64-bit Python.
exit /b 11
:compile_error
echo [ERROR] Python compilation failed.
exit /b 9

:download_assets
if not exist "%~dp0scripts\download_assets.ps1" (
  echo [ERROR] scripts\download_assets.ps1 is missing. The project folder is incomplete.
  exit /b 6
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\download_assets.ps1"
exit /b %ERRORLEVEL%

:assets_only
call :download_assets
exit /b %ERRORLEVEL%
