@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "PYTHON=%CD%\.venv\Scripts\python.exe"
set "ACTIVATE_PS1=%CD%\.venv\Scripts\Activate.ps1"
set "PIP=%CD%\.venv\Scripts\pip.exe"
set "BASE_PYTHON="
set "SERVER_FILE=%CD%\server.py"
set "BACKEND_PORT=5050"
set "FRONTEND_PORT=8080"
set "DASHBOARD_PATH=dashboard.html"
set "DASHBOARD_FILE=%CD%\%DASHBOARD_PATH%"
set "DATA_DIR=%CD%\results\Data"
set "BACKEND_PID_FILE=%DATA_DIR%\dashboard_backend.pid"
set "FRONTEND_PID_FILE=%DATA_DIR%\dashboard_frontend.pid"
set "BACKEND_OUT_LOG=%DATA_DIR%\dashboard_backend.out.log"
set "BACKEND_ERR_LOG=%DATA_DIR%\dashboard_backend.err.log"
set "FRONTEND_OUT_LOG=%DATA_DIR%\dashboard_frontend.out.log"
set "FRONTEND_ERR_LOG=%DATA_DIR%\dashboard_frontend.err.log"
set "DEBUG_FLAG=0"

if /i "%~1"=="--debug" set "DEBUG_FLAG=1"

echo [0/8] Preflight checks...
echo      Workspace: %CD%

if not exist "%PYTHON%" (
  echo [SETUP] Local virtual environment not found. Creating .venv...

  where py >nul 2>&1
  if not errorlevel 1 set "BASE_PYTHON=py -3"

  if not defined BASE_PYTHON (
    where python >nul 2>&1
    if not errorlevel 1 set "BASE_PYTHON=python"
  )

  if not defined BASE_PYTHON (
    echo [ERROR] Python was not found in PATH.
    echo         Install Python 3.10+ and run this file again.
    pause
    exit /b 1
  )

  %BASE_PYTHON% -m venv "%CD%\.venv" || (
    echo [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
  )
)

if not exist "%PYTHON%" (
  echo [ERROR] Python venv not found at:
  echo         %PYTHON%
  pause
  exit /b 1
)

if not exist "%PIP%" (
  echo [ERROR] pip was not found inside .venv.
  pause
  exit /b 1
)

if not exist "%ACTIVATE_PS1%" (
  echo [SETUP] Activate script missing. Recreating dependencies directly with venv python.
)

if exist "%CD%\requirements.txt" (
  echo [SETUP] Ensuring dependencies are installed from requirements.txt...
  "%PYTHON%" -m pip install --upgrade pip >nul 2>&1
  "%PYTHON%" -m pip install -r "%CD%\requirements.txt" || (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
  )
)

if not exist "%SERVER_FILE%" (
  echo [ERROR] Backend file missing:
  echo         %SERVER_FILE%
  pause
  exit /b 1
)

if not exist "%DASHBOARD_FILE%" (
  echo [ERROR] Dashboard file missing:
  echo         %DASHBOARD_FILE%
  pause
  exit /b 1
)

if not exist "%CD%\stop_dashboard_servers.bat" (
  echo [ERROR] stop_dashboard_servers.bat not found in workspace.
  pause
  exit /b 1
)

if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"

echo [1/8] Activating Python environment...
if exist "%ACTIVATE_PS1%" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%ACTIVATE_PS1%'; python --version" || (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
  )
) else (
  "%PYTHON%" --version || (
    echo [ERROR] Python in .venv is not working.
    pause
    exit /b 1
  )
)

echo [2/8] Stopping previous managed servers...
if exist "%CD%\stop_dashboard_servers.bat" (
  call "%CD%\stop_dashboard_servers.bat" --quiet
)

echo [3/8] Cleaning stale listeners and orphan scan processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ports=@(%BACKEND_PORT%,%FRONTEND_PORT%); foreach($p in $ports){ Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { try { Stop-Process -Id $_ -Force -ErrorAction Stop } catch {} } }" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'server\.py' -or $_.CommandLine -match 'pkscreener\.pkscreenercli' -or $_.CommandLine -match '-m\s+http\.server\s+%FRONTEND_PORT%' } | Select-Object -ExpandProperty ProcessId -Unique | ForEach-Object { try { taskkill /PID $_ /T /F | Out-Null } catch {} }" >nul 2>&1
timeout /t 1 >nul

echo [4/8] Starting PKScreener backend on http://127.0.0.1:%BACKEND_PORT% ...
set "BACKEND_PID="
set "BACKEND_PID_TMP=%DATA_DIR%\dashboard_backend.pid.tmp"
if exist "%BACKEND_PID_TMP%" del /q "%BACKEND_PID_TMP%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:DASHBOARD_PORT='%BACKEND_PORT%'; if('%DEBUG_FLAG%' -eq '1'){ $env:DASHBOARD_DEBUG='1' } else { Remove-Item Env:DASHBOARD_DEBUG -ErrorAction SilentlyContinue }; $p = Start-Process -FilePath '%PYTHON%' -ArgumentList @('server.py') -WorkingDirectory '%CD%' -WindowStyle Hidden -PassThru -RedirectStandardOutput '%BACKEND_OUT_LOG%' -RedirectStandardError '%BACKEND_ERR_LOG%'; Set-Content -Path '%BACKEND_PID_TMP%' -Value $p.Id -NoNewline" >nul 2>&1
if exist "%BACKEND_PID_TMP%" set /p BACKEND_PID=<"%BACKEND_PID_TMP%"
if exist "%BACKEND_PID_TMP%" del /q "%BACKEND_PID_TMP%" >nul 2>&1

if not defined BACKEND_PID (
  echo [ERROR] Failed to start backend process.
  pause
  exit /b 1
)
> "%BACKEND_PID_FILE%" echo %BACKEND_PID%

echo [5/8] Starting local HTML server on http://127.0.0.1:%FRONTEND_PORT% ...
set "FRONTEND_PID="
set "FRONTEND_PID_TMP=%DATA_DIR%\dashboard_frontend.pid.tmp"
if exist "%FRONTEND_PID_TMP%" del /q "%FRONTEND_PID_TMP%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process -FilePath '%PYTHON%' -ArgumentList @('-m','http.server','%FRONTEND_PORT%') -WorkingDirectory '%CD%' -WindowStyle Hidden -PassThru -RedirectStandardOutput '%FRONTEND_OUT_LOG%' -RedirectStandardError '%FRONTEND_ERR_LOG%'; Set-Content -Path '%FRONTEND_PID_TMP%' -Value $p.Id -NoNewline" >nul 2>&1
if exist "%FRONTEND_PID_TMP%" set /p FRONTEND_PID=<"%FRONTEND_PID_TMP%"
if exist "%FRONTEND_PID_TMP%" del /q "%FRONTEND_PID_TMP%" >nul 2>&1

if not defined FRONTEND_PID (
  echo [ERROR] Failed to start frontend process.
  call "%CD%\stop_dashboard_servers.bat" --quiet
  pause
  exit /b 1
)
> "%FRONTEND_PID_FILE%" echo %FRONTEND_PID%

echo [6/8] Waiting for backend health check...
set "READY="
for /l %%I in (1,1,60) do (
  "%PYTHON%" -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%BACKEND_PORT%/health', timeout=2).getcode()==200 else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "READY=1"
    goto :ready
  )
  timeout /t 1 >nul
)

:ready
echo [7/8] Launching dashboard...
start "" "http://127.0.0.1:%FRONTEND_PORT%/%DASHBOARD_PATH%?api=http://127.0.0.1:%BACKEND_PORT%"

if defined READY (
  echo.
  echo [OK] Everything is running.
  echo      Backend : http://127.0.0.1:%BACKEND_PORT%/health
  echo      Frontend: http://127.0.0.1:%FRONTEND_PORT%/%DASHBOARD_PATH%
  echo      Backend PID : %BACKEND_PID%
  echo      Frontend PID: %FRONTEND_PID%
  echo      Backend logs : %BACKEND_OUT_LOG%
  echo      Frontend logs: %FRONTEND_OUT_LOG%
  if "%DEBUG_FLAG%"=="1" echo      Mode    : DEBUG logging enabled in backend terminal
) else (
  echo.
  echo [WARN] Backend did not become healthy within timeout.
  echo        Frontend was still opened. Check logs for details:
  echo        %BACKEND_ERR_LOG%
)

echo [8/8] Startup sequence complete.
echo.
echo To stop all managed services: stop_dashboard_servers.bat
exit /b 0
