@echo off
setlocal
if "%~1"=="" (
    echo NiGHTS Twin Seeds Palm Restoration
    echo.
    echo Drag your ORIGINAL NIGHTS.CUE onto this batch file.
    echo Python 3 must be installed. See README.txt.
    echo.
    pause
    exit /b 2
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0Patch_NiGHTS_Palm.py" "%~f1"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo ERROR: Python 3 not found. See README.txt.
        pause
        exit /b 2
    )
    python "%~dp0Patch_NiGHTS_Palm.py" "%~f1"
)
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (
    echo Finished. Boot NIGHTS_PATCHED.CUE. See README.txt for renaming.
) else (
    echo Patch failed. Read the error above; original files were not changed.
)
pause
exit /b %RESULT%
