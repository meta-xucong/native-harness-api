@echo off
setlocal
if not exist "%~dp0runtime\python.exe" (
  echo Missing bundled Python. Extract the complete ZIP first.
  exit /b 2
)
if "%~1"=="" (
  echo WorkBuddy local probe. No model call is started automatically.
  echo Edit a copy of config.example.json with your own absolute paths.
  echo Commands:
  echo   Probe.cmd setup --config "C:\your\config.json"
  echo   Probe.cmd doctor --config "C:\your\config.json"
  echo   Probe.cmd run --config "C:\your\config.json" --prompt-file "C:\your\prompt.txt"
  echo   Probe.cmd inspect --config "C:\your\config.json"
  echo   Probe.cmd export --config "C:\your\config.json" --output "C:\your\report.json"
  pause
  exit /b 0
)
"%~dp0runtime\python.exe" -I "%~dp0launcher.py" %*
exit /b %errorlevel%
