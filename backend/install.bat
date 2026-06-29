@echo off
REM Backend installer for home-bookshelf (Windows CMD).
REM Creates the venv, installs requirements, and runs DB migrations.
REM Usage:  backend\install.bat        (from repo root)
REM          install.bat                (from backend\)
setlocal enableextensions enabledelayedexpansion
chcp 65001 >nul

cd /d "%~dp0"

REM Pick a Python interpreter.
where python >nul 2>&1 && ( set "PY=python" ) || ( ^
  where py >nul 2>&1 && ( set "PY=py -3" ) || ( ^
    echo [ERROR] Neither 'python' nor 'py' found. Install Python 3.10+ first. ^
    exit /b 1 ^
  ) ^
)

echo ==^> Interpreter:
%PY% --version
if errorlevel 1 ( echo [ERROR] Python check failed. & exit /b 1 )

set "VENV=.venv"
set "VENV_PY=%VENV%\Scripts\python.exe"

REM If the venv exists but has no Windows interpreter (e.g. a macOS/Linux
REM venv synced from another machine), rebuild it.
if not exist "%VENV_PY%" (
  if exist "%VENV%" (
    echo ==^> Found an incompatible virtualenv (missing %VENV_PY%). Rebuilding...
    rmdir /s /q "%VENV%"
  )
  echo ==^> Creating virtualenv: %VENV%
  %PY% -m venv %VENV%
  if errorlevel 1 ( echo [ERROR] venv creation failed. & exit /b 1 )
)

echo ==^> Upgrading pip
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 ( echo [ERROR] pip upgrade failed. & exit /b 1 )

echo ==^> Installing dependencies ^(requirements.txt^)
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 ( echo [ERROR] dependency install failed. & exit /b 1 )

echo ==^> Running database migrations ^(alembic upgrade head^)
"%VENV_PY%" -m alembic upgrade head
if errorlevel 1 ( echo [ERROR] alembic migration failed. & exit /b 1 )

echo.
echo [OK] Backend setup complete.
echo    Activate:  .venv\Scripts\activate
echo    Start:     .venv\Scripts\uvicorn.exe app.main:app --reload --host 127.0.0.1 --port 8000 --app-dir .
echo    Docs:      http://127.0.0.1:8000/docs
echo.
echo NOTE (barcode recognition): pyzbar needs zbar.dll at runtime.
echo    Install ZBar for Windows, or place zbar.dll in backend\ and add it to PATH.

endlocal
