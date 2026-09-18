@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if not %errorlevel%==0 (
        echo No Python found on PATH. Install Python 3 and try again.
        exit /b 1
    )
    set "PY=python"
)

%PY% -m haven.web.server %*
