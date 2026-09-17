@echo off
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0io_gateway.py" %*
  exit /b %errorlevel%
)
python "%~dp0io_gateway.py" %*
exit /b %errorlevel%
