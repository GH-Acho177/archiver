@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo  Media Downloader -- Release Build
echo ============================================================
echo.

:: ── 1. Check prerequisites ────────────────────────────────────────────────────
where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PyInstaller not found.  Run:  pip install pyinstaller
    pause & exit /b 1
)

if not exist "packaging\gallery-dl.exe" (
    echo [WARN]  gallery-dl.exe not found in packaging\.
    echo         Download from: https://github.com/mikf/gallery-dl/releases
    echo         Place gallery-dl.exe in the packaging\ folder, then re-run.
    echo.
)

if not exist "packaging\yt-dlp.exe" (
    echo [WARN]  yt-dlp.exe not found in packaging\.
    echo         Download from: https://github.com/yt-dlp/yt-dlp/releases
    echo         Place yt-dlp.exe in the packaging\ folder, then re-run.
    echo.
)

if not exist "icon.ico" (
    echo [WARN]  icon.ico not found -- the exe will use the default Python icon.
    echo         Add a 256x256 icon.ico next to build.bat to brand the app.
    echo.
)

:: ── 2. PyInstaller build ──────────────────────────────────────────────────────
echo [1/2] Building exe with PyInstaller...
pyinstaller packaging\MediaDownloader.spec --clean --noconfirm --workpath build --distpath dist
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed.  See output above.
    pause & exit /b 1
)
echo       Done.  Output: dist\MediaDownloader\
echo.

:: ── 3. Inno Setup installer (optional) ───────────────────────────────────────
set ISCC=""
for %%P in (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    "C:\Program Files\Inno Setup 6\ISCC.exe"
    "C:\Program Files (x86)\Inno Setup 5\ISCC.exe"
) do (
    if exist %%P set ISCC=%%P
)

if !ISCC!=="" (
    echo [2/2] Inno Setup not found -- skipping installer creation.
    echo       To create the installer:
    echo         1. Install Inno Setup from https://jrsoftware.org/isinfo.php
    echo         2. Re-run build.bat  -or-  open installer.iss in Inno Setup IDE
) else (
    echo [2/2] Creating installer with Inno Setup...
    !ISCC! packaging\installer.iss
    if errorlevel 1 (
        echo [ERROR] Inno Setup failed.  See output above.
        pause & exit /b 1
    )
    echo       Done.  Installer: dist\MediaDownloader_Setup_1.0.0.exe
)

echo.
echo ============================================================
echo  Build complete.
echo ============================================================
pause
