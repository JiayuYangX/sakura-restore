@echo off
cd /d "%~dp0"

REM Find MSYS2's 32-bit g++ (required: SSP is 32-bit)
set GXX=
for %%r in (C:\msys64 C:\msys2) do if exist "%%r\mingw32\bin\g++.exe" set GXX=%%r\mingw32\bin\g++.exe&set GXXDIR=%%r\mingw32\bin

if "%GXX%"=="" (
    echo ERROR: MSYS2 MinGW32 g++ not found.
    echo Install from https://www.msys2.org/ then:
    echo   pacman -S mingw-w64-i686-gcc
    pause
    exit /b 1
)

echo Generating replace_table.inc...
python generate_replace_table.py
if %errorlevel% neq 0 pause & exit /b %errorlevel%

echo Compiling makoto.dll (32-bit)...
if not exist output mkdir output
set PATH=%GXXDIR%;%PATH%
"%GXX%" -shared -o output\makoto.dll makoto.cpp -O2 -static -s
if %errorlevel% neq 0 pause & exit /b %errorlevel%

echo SUCCESS: output\makoto.dll
if not "%1"=="" copy /y output\makoto.dll "%1\makoto.dll" >nul
pause
