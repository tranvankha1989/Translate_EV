@echo off
setlocal
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

py -3.11 -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo Khong the cai dependency.
    pause
    exit /b 1
)

py -3.11 -m unittest test_features test_translator test_app_log
if errorlevel 1 (
    echo Test that bai, dung build.
    pause
    exit /b 1
)

py -3.11 icons.py icon.ico
if errorlevel 1 (
    echo Khong tao duoc icon.
    pause
    exit /b 1
)

py -3.11 -m PyInstaller --noconfirm --clean --onefile --windowed --name TranslateVietnam --icon icon.ico app.py
if errorlevel 1 (
    echo Build that bai.
    pause
    exit /b 1
)

echo.
echo Da tao dist\TranslateVietnam.exe
pause
