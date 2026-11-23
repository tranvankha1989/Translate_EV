@echo off
rem Build ban exe doc lap: dist\TranslateVietnam.exe (may nguoi dung khong can cai Python).
setlocal
cd /d "%~dp0.."
set "ROOT=%CD%"

py -3.11 -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo Khong the cai dependency.
    pause
    exit /b 1
)

py -3.11 -m unittest discover -s tests -t .
if errorlevel 1 (
    echo Test that bai, dung build.
    pause
    exit /b 1
)

if not exist build mkdir build
py -3.11 -m translate_vietnam.icons build\icon.ico
if errorlevel 1 (
    echo Khong tao duoc icon.
    pause
    exit /b 1
)

py -3.11 -m PyInstaller --noconfirm --clean --onefile --windowed --name TranslateVietnam ^
    --icon "%ROOT%\build\icon.ico" ^
    --add-data "%ROOT%\translate_vietnam\assets;translate_vietnam\assets" ^
    --specpath build --workpath build --distpath dist "%ROOT%\app.py"
if errorlevel 1 (
    echo Build that bai.
    pause
    exit /b 1
)

echo.
echo Da tao dist\TranslateVietnam.exe
pause
