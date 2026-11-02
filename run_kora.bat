@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ================================================================
echo KORA - START
echo ================================================================
if not exist "src\main.py" (
  echo [ERROR] src\main.py is missing. The project folder is incomplete.
  exit /b 10
)
if not exist ".venv\Scripts\python.exe" (
  echo [KORA] Python environment missing. Running setup...
  call "%~dp0setup_kora.bat"
  if errorlevel 1 (
    echo [ERROR] Setup failed with code %ERRORLEVEL%.
    exit /b %ERRORLEVEL%
  )
)

rem Download any missing AI models or llama.cpp runtime automatically (one pass).
set "NEED_ASSETS="
".venv\Scripts\python.exe" src\diagnose.py --models-only >nul 2>&1
if errorlevel 1 set "NEED_ASSETS=1"
call :check_runtime
if not defined RUNTIME_FOUND set "NEED_ASSETS=1"
if defined NEED_ASSETS (
  echo [KORA] Some AI files are missing. Downloading them now, this can take a few minutes...
  call "%~dp0setup_kora.bat" --assets-only
  call :check_runtime
)

".venv\Scripts\python.exe" src\diagnose.py --models-only
if errorlevel 1 (
  echo [ERROR] Required model files are missing and could not be downloaded.
  echo         Check your internet connection and run setup_kora.bat again,
  echo         or put the exact configured GGUF filenames in data\models\.
  pause
  exit /b 2
)

if not defined RUNTIME_FOUND (
  echo [ERROR] llama-server.exe was not found and could not be downloaded.
  echo         Check your internet connection and run setup_kora.bat again,
  echo         or put the Windows llama.cpp server and its DLLs in data\runtime\vulkan\.
  pause
  exit /b 4
)

rem Catch native-extension failures before the full application startup.
".venv\Scripts\python.exe" -c "import pymupdf"
if errorlevel 1 (
  echo [WARN] PyMuPDF failed to import. Re-running setup to repair the environment...
  call "%~dp0setup_kora.bat"
  if errorlevel 1 exit /b %ERRORLEVEL%
)

rem Make freshly installed Tesseract and LibreOffice visible to this window.
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "PATH=%ProgramFiles%\Tesseract-OCR;%PATH%"
if exist "%ProgramFiles%\LibreOffice\program\soffice.exe" set "PATH=%ProgramFiles%\LibreOffice\program;%PATH%"

echo [KORA] Launching the application...
".venv\Scripts\python.exe" src\main.py
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [KORA] Application exited with code %RC%.
pause
exit /b %RC%

:check_runtime
set "RUNTIME_FOUND="
if exist "data\runtime\llama-server.exe" set "RUNTIME_FOUND=1"
for %%D in (cuda vulkan sycl cpu) do if exist "data\runtime\%%D\llama-server.exe" set "RUNTIME_FOUND=1"
exit /b 0
