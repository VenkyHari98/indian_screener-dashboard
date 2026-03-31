@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "QUIET=0"
if /i "%~1"=="--quiet" set "QUIET=1"

set "BACKEND_PORT=5050"
set "FRONTEND_PORT=8080"
set "DATA_DIR=%CD%\results\Data"
set "BACKEND_PID_FILE=%DATA_DIR%\dashboard_backend.pid"
set "FRONTEND_PID_FILE=%DATA_DIR%\dashboard_frontend.pid"

if "%QUIET%"=="0" echo [0/2] Stopping managed processes from PID files...

if exist "%BACKEND_PID_FILE%" (
  set /p BACKEND_PID=<"%BACKEND_PID_FILE%"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Stop-Process -Id %BACKEND_PID% -Force -ErrorAction Stop } catch {}" >nul 2>&1
  del /q "%BACKEND_PID_FILE%" >nul 2>&1
)

if exist "%FRONTEND_PID_FILE%" (
  set /p FRONTEND_PID=<"%FRONTEND_PID_FILE%"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Stop-Process -Id %FRONTEND_PID% -Force -ErrorAction Stop } catch {}" >nul 2>&1
  del /q "%FRONTEND_PID_FILE%" >nul 2>&1
)

if "%QUIET%"=="0" echo [1/2] Clearing listeners on ports %BACKEND_PORT% and %FRONTEND_PORT%...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ports=@(%BACKEND_PORT%,%FRONTEND_PORT%); foreach($p in $ports){ Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { try { Stop-Process -Id $_ -Force -ErrorAction Stop } catch {} } }" >nul 2>&1

if "%QUIET%"=="0" echo [2/2] Clearing orphan scanner/backend python processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'server\.py' -or $_.CommandLine -match 'pkscreener\.pkscreenercli' -or $_.CommandLine -match '-m\s+http\.server\s+%FRONTEND_PORT%' } | Select-Object -ExpandProperty ProcessId -Unique | ForEach-Object { try { taskkill /PID $_ /T /F | Out-Null } catch {} }" >nul 2>&1

if exist "%BACKEND_PID_FILE%" del /q "%BACKEND_PID_FILE%" >nul 2>&1
if exist "%FRONTEND_PID_FILE%" del /q "%FRONTEND_PID_FILE%" >nul 2>&1

if "%QUIET%"=="0" (
  echo Servers stopped.
  echo Done.
)

exit /b 0
