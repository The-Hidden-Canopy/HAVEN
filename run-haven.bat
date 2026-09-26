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
        goto FAILED
    )
    set "PY=python"
)

set "NATIVE_EXE=%~dp0native\Haven.Desktop\bin\x64\Debug\net8.0-windows10.0.19041.0\Haven.Desktop.exe"

where dotnet >nul 2>nul
if errorlevel 1 (
    if not exist "%NATIVE_EXE%" (
        echo HAVEN native client is not built and dotnet was not found on PATH.
        echo Install the .NET 8 SDK, or pass --web for the compatibility surface: run-haven.bat --web
        goto FAILED
    )
    echo WARNING: dotnet not found; launching the existing native build without rebuilding.
    goto LAUNCH
)

REM A running client locks the build output; the client is disposable (the
REM resident core keeps state), so close it before rebuilding.
taskkill.exe /F /IM Haven.Desktop.exe >nul 2>nul

REM Incremental: a no-op when nothing changed, so the launcher always runs
REM the freshest native client instead of whatever binary happened to be there.
dotnet build -p:Platform=x64 "%~dp0native\Haven.Desktop\Haven.Desktop.csproj"
if errorlevel 1 (
    echo.
    echo Native build failed. Fix the build errors above, or pass --web for the
    echo compatibility surface: run-haven.bat --web
    goto FAILED
)

:LAUNCH
%PY% -m haven.desktop %*
set "EXITCODE=%errorlevel%"
if not "%EXITCODE%"=="0" (
    echo.
    echo HAVEN exited with code %EXITCODE%.
    goto FAILED
)
goto END

:FAILED
REM Double-clicked from Explorer, this window would otherwise close the
REM instant the script exits - hiding every message printed above before
REM anyone can read it. Keep it open until dismissed.
echo.
pause
exit /b 1

:END
