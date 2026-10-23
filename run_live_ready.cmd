@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" "scripts\run_live_ready.py" %*
set "bot_exit_code=%errorlevel%"
if not "%bot_exit_code%"=="0" (
    echo Bot startup stopped. Review the message above before retrying.
    pause
)
exit /b %bot_exit_code%
