@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

if not exist "logs" mkdir "logs"

for /f %%L in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "RUN_ID=%%L"
set "LOG_FILE=logs\task_scheduler.log"
set "LOG_STATUS=ok"
powershell -NoProfile -Command "try { Add-Content -LiteralPath 'logs\task_scheduler.log' -Value '' -ErrorAction Stop; exit 0 } catch { exit 1 }" >nul 2>nul
if errorlevel 1 set "LOG_STATUS=blocked"
if /I "!LOG_STATUS!"=="blocked" (
  set "LOG_FILE=logs\task_scheduler_!RUN_ID!.log"
  echo [%date% %time%] WARN: task_scheduler.log bloqueado; se usa log alterno "!LOG_FILE!".>>"!LOG_FILE!"
)

set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "venv\Scripts\python.exe" set "PYTHON_EXE=venv\Scripts\python.exe"

if not defined PYTHON_EXE (
  echo [%date% %time%] ERROR: No existe un interprete valido en .venv\Scripts\python.exe ni venv\Scripts\python.exe>>"!LOG_FILE!"
  exit /b 9009
)

set "LOCK_DIR=logs\scheduler.lock"
set "LOCK_MAX_AGE_MINUTES=180"
mkdir "%LOCK_DIR%" 2>nul
if errorlevel 1 (
  set "LOCK_STATUS=unknown"
  for /f %%A in ('powershell -NoProfile -Command "$lock = Get-Item -LiteralPath $env:LOCK_DIR -ErrorAction SilentlyContinue; if (-not $lock) { Write-Output missing } elseif (((Get-Date) - $lock.CreationTime).TotalMinutes -ge [double]$env:LOCK_MAX_AGE_MINUTES) { Write-Output stale } else { Write-Output active }"') do set "LOCK_STATUS=%%A"
  if /I "!LOCK_STATUS!"=="stale" (
    echo [%date% %time%] WARN: Lock huerfano detectado; se elimina automaticamente.>>"!LOG_FILE!"
    rmdir "%LOCK_DIR%" >nul 2>&1
    mkdir "%LOCK_DIR%" 2>nul
  )
  if errorlevel 1 (
    echo [%date% %time%] INFO: Scheduler en ejecucion, se omite corrida solapada.>>"!LOG_FILE!"
    exit /b 0
  )
)

echo [%date% %time%] START run_scheduler.py python="%PYTHON_EXE%">>"!LOG_FILE!"
"%PYTHON_EXE%" -u "run_scheduler.py" >>"!LOG_FILE!" 2>&1
set "RC=!ERRORLEVEL!"
>>"!LOG_FILE!" echo [%date% %time%] END run_scheduler.py rc=!RC!

rmdir "%LOCK_DIR%" >nul 2>&1
exit /b !RC!
