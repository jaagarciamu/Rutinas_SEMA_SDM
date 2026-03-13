@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

if not exist "logs" mkdir "logs"

set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "venv\Scripts\python.exe" set "PYTHON_EXE=venv\Scripts\python.exe"

if not defined PYTHON_EXE (
  echo [%date% %time%] ERROR: No existe un interprete valido en .venv\Scripts\python.exe ni venv\Scripts\python.exe>>"logs\task_scheduler.log"
  exit /b 9009
)

set "LOCK_DIR=logs\scheduler.lock"
mkdir "%LOCK_DIR%" 2>nul
if errorlevel 1 (
  echo [%date% %time%] INFO: Scheduler en ejecucion, se omite corrida solapada.>>"logs\task_scheduler.log"
  exit /b 0
)

echo [%date% %time%] START run_scheduler.py python="%PYTHON_EXE%">>"logs\task_scheduler.log"
"%PYTHON_EXE%" -u "run_scheduler.py" >>"logs\task_scheduler.log" 2>&1
set "RC=!ERRORLEVEL!"
>>"logs\task_scheduler.log" echo [%date% %time%] END run_scheduler.py rc=!RC!

rmdir "%LOCK_DIR%" >nul 2>&1
exit /b !RC!
