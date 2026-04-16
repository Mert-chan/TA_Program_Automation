@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"
echo Current directory: %cd%
echo.

REM Find Python
set "PYTHON_BASE="
where py >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_BASE=py"
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo Error: Neither 'py' launcher nor 'python' found on PATH.
        echo Please install Python 3.11+ and ensure it is on PATH.
        goto :pause_and_exit
    )
    set "PYTHON_BASE=python"
)

REM Get Python version
set "PY_VERSION="
for /f "tokens=2" %%V in ('%PYTHON_BASE% --version 2^>^&1') do set "PY_VERSION=%%V"

if not defined PY_VERSION (
    echo Error: Could not read Python version.
    goto :pause_and_exit
)

set "PY_MAJOR=" & set "PY_MINOR="
for /f "tokens=1,2 delims=." %%A in ("!PY_VERSION!") do (
    set "PY_MAJOR=%%A"
    set "PY_MINOR=%%B"
)
if not defined PY_MINOR set "PY_MINOR=0"

set /a "PY_MAJOR_NUM=!PY_MAJOR!" >nul 2>&1
set /a "PY_MINOR_NUM=!PY_MINOR!" >nul 2>&1

if !PY_MAJOR_NUM! LSS 3 (
    echo Error: Python 3.11+ required. Detected !PY_VERSION!.
    goto :pause_and_exit
)
if !PY_MAJOR_NUM! EQU 3 if !PY_MINOR_NUM! LSS 11 (
    echo Error: Python 3.11+ required. Detected !PY_VERSION!.
    goto :pause_and_exit
)

if /I "%PYTHON_BASE%"=="py" (
    set "PYTHON_CMD=py -3.!PY_MINOR_NUM!"
) else (
    set "PYTHON_CMD=python"
)

echo Detected Python !PY_VERSION!. Using: %PYTHON_CMD%
echo.

REM Install dependencies (cached by stamp flag)
set "REQ_FILE=%cd%\requirements.txt"
set "FLAG_FILE=%cd%\.requirements_installed.flag"

if not exist "%REQ_FILE%" (
    echo Error: requirements.txt not found.
    goto :pause_and_exit
)

for %%I in ("%REQ_FILE%") do set "REQ_STAMP=%%~tI"

if exist "%FLAG_FILE%" (
    set "STORED_STAMP="
    set /p "STORED_STAMP=" < "%FLAG_FILE%"
    if /I "!STORED_STAMP!"=="!REQ_STAMP!" (
        echo Dependencies already installed. Skipping pip install.
        goto :launch_app
    )
)

echo Installing dependencies...
call %PYTHON_CMD% -m pip install --upgrade pip
if errorlevel 1 (echo Error: pip upgrade failed. & goto :pause_and_exit)

call %PYTHON_CMD% -m pip install -r "%REQ_FILE%"
if errorlevel 1 (echo Error: dependency install failed. & goto :pause_and_exit)

> "%FLAG_FILE%" echo !REQ_STAMP!
echo Dependencies installed.
echo.

:launch_app
echo Launching TA Program Automation...
echo.
call %PYTHON_CMD% -m streamlit run "%cd%\app.py"
echo.
echo ===== Session Ended =====

:pause_and_exit
echo.
echo Press any key to close...
pause >nul
endlocal
exit /b
