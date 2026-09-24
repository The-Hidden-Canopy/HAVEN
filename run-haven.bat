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

set "NATIVE_EXE=%~dp0native\Haven.Desktop\bin\x64\Debug\net8.0-windows10.0.19041.0\Haven.Desktop.exe"

if not exist "%NATIVE_EXE%" (
    where dotnet >nul 2>nul
    if not errorlevel 1 (
        echo HAVEN native client is not built yet; building it now ^(one-time^)...
        dotnet build -p:Platform=x64 "%~dp0native\Haven.Desktop\Haven.Desktop.csproj" || (
            echo.
            echo Native build failed. Install the .NET 8 SDK ^(with Windows App SDK workload^) or
            echo pass --web for the compatibility surface: run-haven.bat --web
            exit /b 1
        )
    ) else (
        echo HAVEN native client is not built and dotnet was not found on PATH.
        echo Install the .NET 8 SDK, or pass --web for the compatibility surface: run-haven.bat --web
        exit /b 1
    )
)

%PY% -m haven.desktop %*
