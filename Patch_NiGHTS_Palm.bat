@echo off
setlocal
if "%~1"=="" (
    echo Drag your original NIGHTS.CUE onto Patch_NiGHTS_Palm.bat.
    pause
    exit /b 1
)
py -3 "%~dp0Patch_NiGHTS_Palm.py" "%~1"
set "status=%ERRORLEVEL%"
if not "%status%"=="0" (
    echo.
    echo Patching failed. Your original game files were not changed.
    echo Make sure Python 3 is installed and the CUE is a supported original NTSC image.
)
echo.
pause
exit /b %status%
