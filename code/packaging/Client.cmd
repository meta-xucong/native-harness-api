@echo off
setlocal
if not "%~1"=="" goto run
"%~dp0runtime\python.exe" -I "%~dp0client.py" --help
echo Read CLIENT-README.zh-CN.md before login or starting the node.
pause
exit /b 0
:run
"%~dp0runtime\python.exe" -I "%~dp0client.py" %*
exit /b %errorlevel%
