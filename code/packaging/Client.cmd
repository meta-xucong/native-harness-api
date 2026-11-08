@echo off
setlocal
if not "%~1"=="" goto run
"%~dp0runtime\python.exe" -I "%~dp0client.py"
set "client_exit=%errorlevel%"
pause
exit /b %client_exit%
:run
"%~dp0runtime\python.exe" -I "%~dp0client.py" %*
exit /b %errorlevel%
