@echo off
setlocal enabledelayedexpansion
REM Always run from this folder
cd /d "%~dp0"
REM UTF-8 encoding for Python (prevents Unicode crashes on Windows console)
set PYTHONIOENCODING=utf-8
echo ============================
echo 9Bot - Setup + Run
echo ============================

REM Download ADB if missing (fallback for source installs without bundled binaries)
if not exist "platform-tools\adb.exe" (
  echo.
  echo Downloading ADB platform-tools...
  powershell -Command "Invoke-WebRequest -Uri 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip' -OutFile '%TEMP%\platform-tools.zip'"
  if errorlevel 1 (
    echo WARNING: Failed to download platform-tools. ADB may not work.
  ) else (
    powershell -Command "Expand-Archive -Path '%TEMP%\platform-tools.zip' -DestinationPath '%TEMP%\pt-extract' -Force"
    if not exist "platform-tools" mkdir "platform-tools"
    copy /Y "%TEMP%\pt-extract\platform-tools\adb.exe" "platform-tools\" >nul
    copy /Y "%TEMP%\pt-extract\platform-tools\AdbWinApi.dll" "platform-tools\" >nul
    copy /Y "%TEMP%\pt-extract\platform-tools\AdbWinUsbApi.dll" "platform-tools\" >nul
    rd /s /q "%TEMP%\pt-extract" 2>nul
    del "%TEMP%\platform-tools.zip" 2>nul
    echo Done!
  )
)

REM Ensure Python 3.13 is installed (required for PaddlePaddle)
py -3.13 -V >nul 2>&1
if errorlevel 1 (
  echo.
  echo Python 3.13 not found. Installing...
  winget install Python.Python.3.13 --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo.
    echo ERROR: Failed to install Python 3.13.
    echo Install manually from https://python.org and make sure "Python Launcher" is checked.
    pause
    exit /b 1
  )
  echo Python 3.13 installed successfully.
)

REM Verify venv uses Python 3.13 — rebuild if it was created with a different version
set VENV_OK=0
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; exit(0 if sys.version_info[:2]==(3,13) else 1)" >nul 2>&1
  if !errorlevel!==0 set VENV_OK=1
)
if !VENV_OK!==0 (
  if exist ".venv" (
    echo.
    echo Existing venv uses wrong Python version. Rebuilding with 3.13...
    rd /s /q ".venv"
  )
  echo.
  echo Creating virtual environment with Python 3.13...
  py -3.13 -m venv .venv
  if errorlevel 1 (
    echo ERROR: Failed to create venv.
    pause
    exit /b 1
  )
)
echo.
echo Activating venv...
call ".venv\Scripts\activate.bat" >nul 2>&1

REM Remove legacy EasyOCR/PyTorch if present (conflicts with PaddlePaddle)
py -c "import easyocr" >nul 2>&1
if not errorlevel 1 (
  echo.
  echo Removing old OCR engine ^(EasyOCR/PyTorch^) to avoid conflicts...
  py -m pip uninstall easyocr torch torchvision torchaudio -y -qq 2>nul
  echo Done.
)

REM Check if first-time setup (paddleocr not installed yet)
set FIRST_RUN=0
py -c "import paddleocr" >nul 2>&1
if errorlevel 1 set FIRST_RUN=1

if %FIRST_RUN%==1 (
  echo.
  echo First-time setup: downloading OCR engine.
  echo This only happens once and may take a few minutes.
  echo.
  py -m pip install --upgrade pip -qq 2>nul
  py -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo ERROR: Failed to install requirements.
    pause
    exit /b 1
  )
) else (
  REM Only install if requirements.txt changed since last install
  set NEEDS_INSTALL=0
  if not exist ".venv\.req_hash" set NEEDS_INSTALL=1
  if !NEEDS_INSTALL!==0 (
    certutil -hashfile requirements.txt MD5 2>nul | findstr /v ":" > "%TEMP%\req_hash_new.txt"
    fc /b ".venv\.req_hash" "%TEMP%\req_hash_new.txt" >nul 2>&1
    if errorlevel 1 set NEEDS_INSTALL=1
  )
  if !NEEDS_INSTALL!==1 (
    echo Installing requirements...
    py -m pip install --upgrade pip -qq 2>nul
    py -m pip install -r requirements.txt -qq
    if errorlevel 1 (
      echo.
      echo ERROR: Failed to install requirements.
      pause
      exit /b 1
    )
    certutil -hashfile requirements.txt MD5 2>nul | findstr /v ":" > ".venv\.req_hash"
  )
)
echo Done!

REM Protocol interception setup prompt (first run only)
if not exist ".venv\.protocol_setup_done" (
  echo.
  echo ==========================================
  echo To run properly 9Bot needs to have protocol
  echo interception installed. Would you like to
  echo continue?
  echo ==========================================
  echo.
  choice /C YN /M "Install protocol interception"
  if !errorlevel!==1 (
    echo.
    echo Starting protocol setup...
    echo Make sure BlueStacks is up and running and that KG is installed.
    echo Do not have the game running during this step. Just have BlueStacks open.
    echo.
    py protocol\patch_apk.py
    if errorlevel 1 (
      echo.
      echo WARNING: Protocol setup failed. You can retry later from the Debug page.
    ) else (
      echo.
      echo Protocol interception installed successfully!
    )
  ) else (
    echo.
    echo Skipped. You can set up protocol interception later from the Debug page.
  )
  echo. > ".venv\.protocol_setup_done"
)

echo.
py updater.py

echo.
py run_web.py
if errorlevel 1 (
  echo.
  echo ==========================================
  echo 9Bot crashed! See error message above.
  echo ==========================================
  pause
  exit /b 1
)
echo.
echo 9Bot exited.
exit /b 0
