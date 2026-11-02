@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

rem Developer-only packaging script. This task does not run a PyInstaller build.
set "DEBUG=0"
set "WITHMODELS=0"
for %%A in (%*) do (
  if /i "%%A"=="debug" set "DEBUG=1"
  if /i "%%A"=="withmodels" set "WITHMODELS=1"
)

if not exist ".venv\Scripts\python.exe" (
  echo [KORA] No .venv found - running setup first...
  call "%CD%\setup_kora.bat"
  if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pyinstaller
if errorlevel 1 exit /b 2
if "%DEBUG%"=="1" (set "KORA_BUILD_CONSOLE=1") else (set "KORA_BUILD_CONSOLE=0")

".venv\Scripts\python.exe" -m PyInstaller build\kora.spec --noconfirm --clean
if errorlevel 1 exit /b 3

set "OUT=dist\KORA"
echo [KORA] Copying the static UI and user-data folder layout...
if not exist "%OUT%\src" mkdir "%OUT%\src"
robocopy "src\web" "%OUT%\src\web" /E /NFL /NDL /NJH /NJS /NP >nul
for %%D in (data data\workspace data\models data\runtime data\runtime\vulkan data\runtime\cuda data\runtime\sycl data\runtime\cpu data\logs data\runtime\webview_storage) do if not exist "%OUT%\%%D" mkdir "%OUT%\%%D"
if exist "data\runtime" robocopy "data\runtime" "%OUT%\data\runtime" /E /XF *.log /XD webview_storage /NFL /NDL /NJH /NJS /NP >nul
if "%WITHMODELS%"=="1" robocopy "data\models" "%OUT%\data\models" /E /NFL /NDL /NJH /NJS /NP >nul

> "%OUT%\README.txt" echo KORA - run KORA.exe. Keep src\web next to the executable.
>> "%OUT%\README.txt" echo Put the configured GGUF files in data\models\.
>> "%OUT%\README.txt" echo Put llama-server.exe and its DLLs in data\runtime\ or a backend subfolder.
>> "%OUT%\README.txt" echo User documents belong in data\workspace\. Logs are in data\logs\.
>> "%OUT%\README.txt" echo Microsoft WebView2 is required for the embedded desktop window.

echo [OK] Built: %CD%\%OUT%\KORA.exe
pause
exit /b 0
