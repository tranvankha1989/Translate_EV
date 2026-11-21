@echo off
set "APP_DIR=%~dp0"
if exist "%APP_DIR%TranslateVietnam.exe" (
    start "Translate Vietnam" "%APP_DIR%TranslateVietnam.exe"
    exit /b 0
)
set "PYTHONW=%LocalAppData%\Programs\Python\Python311\pythonw.exe"
if not exist "%PYTHONW%" set "PYTHONW=pythonw.exe"
start "Translate Vietnam" "%PYTHONW%" "%APP_DIR%app.py"
