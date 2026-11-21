@echo off
rem Phat hanh ban moi len GitHub Releases (chi can tren may nha phat trien).
rem Truoc khi chay: tang APP_VERSION trong config.py, commit, (tuy chon) ghi chu vao release_notes.md.
rem Can: Python 3.11, git, GitHub CLI (gh) da dang nhap. Nguoi dung app KHONG can cac cong cu nay.
setlocal
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

where gh >nul 2>nul || (echo Chua cai GitHub CLI: https://cli.github.com & pause & exit /b 1)
gh auth status >nul 2>nul || (echo Hay chay "gh auth login" truoc. & pause & exit /b 1)

for /f "delims=" %%v in ('py -3.11 -c "import config; print(config.APP_VERSION)"') do set "VERSION=%%v"
for /f "delims=" %%r in ('py -3.11 -c "import config; print(config.UPDATE_REPO)"') do set "REPO=%%r"
if "%VERSION%"=="" (echo Khong doc duoc APP_VERSION. & pause & exit /b 1)
echo Phat hanh phien ban %VERSION% len %REPO%

gh release view v%VERSION% -R %REPO% >nul 2>nul && (
    echo Ban v%VERSION% da phat hanh. Hay tang APP_VERSION trong config.py.
    pause & exit /b 1
)

git diff --quiet && git diff --cached --quiet || (
    echo Con thay doi chua commit. Hay commit truoc khi phat hanh.
    pause & exit /b 1
)
git push || (echo Khong day duoc code len GitHub. & pause & exit /b 1)

py -3.11 -m pip install -r requirements.txt pyinstaller || (echo Khong cai duoc thu vien. & pause & exit /b 1)
py -3.11 -m unittest test_features test_translator test_app_log test_updater || (echo Test that bai. & pause & exit /b 1)
py -3.11 icons.py icon.ico || (echo Khong tao duoc icon. & pause & exit /b 1)
py -3.11 -m PyInstaller --noconfirm --clean --onefile --windowed --name TranslateVietnam --icon icon.ico app.py || (
    echo Build that bai. & pause & exit /b 1
)
py -3.11 -c "import hashlib; d=open(r'dist\TranslateVietnam.exe','rb').read(); open(r'dist\TranslateVietnam.exe.sha256','w').write(hashlib.sha256(d).hexdigest()+'  TranslateVietnam.exe\n')"

set "NOTES=--generate-notes"
if exist release_notes.md set "NOTES=--notes-file release_notes.md"
gh release create v%VERSION% dist\TranslateVietnam.exe dist\TranslateVietnam.exe.sha256 -R %REPO% --title "Translate Vietnam %VERSION%" %NOTES% || (
    echo Tao ban phat hanh that bai. & pause & exit /b 1
)

echo.
echo Da phat hanh v%VERSION%. App cua nguoi dung se tu bao co ban moi trong vong 1 ngay.
pause
