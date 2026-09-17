@echo off
REM Compile the HAVEN-BT WinRT backend into a real DLL.
REM
REM Usage: build_windows.cmd [output_dll_path]
REM
REM Requires MSVC (Visual Studio Build Tools, C++ workload) and the Windows
REM SDK's C++/WinRT projection headers -- both of which ship together with
REM the "Desktop development with C++" workload. This is a real platform
REM backend (Windows.Devices.Bluetooth via WinRT), unlike
REM src/fixture/fixture_backend.c.
setlocal

set SCRIPT_DIR=%~dp0
set OUT=%~1
if "%OUT%"=="" set OUT=%SCRIPT_DIR%build\havenbt_winrt.dll

for /f "usebackq tokens=*" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set VSINSTALL=%%i
if "%VSINSTALL%"=="" (
    echo No Visual Studio installation with the C++ Tools component was found. 1>&2
    echo Install "Visual Studio Build Tools" with the "Desktop development with C++" workload. 1>&2
    exit /b 1
)

call "%VSINSTALL%\VC\Auxiliary\Build\vcvars64.bat" > nul
if not exist "%SCRIPT_DIR%build" mkdir "%SCRIPT_DIR%build"

cl.exe /std:c++20 /EHsc /await:strict /W3 ^
    /I"%SCRIPT_DIR%include" ^
    /LD /Fo:"%SCRIPT_DIR%build\\" /Fe:"%OUT%" ^
    "%SCRIPT_DIR%src\platform\windows\winrt_backend.cpp" ^
    /link WindowsApp.lib
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%

echo built: %OUT%
