@echo off
REM Start the dev server on the local network so phones on the same Wi-Fi can open it.
cd /d "%~dp0"
echo.
echo Open one of these on your phone (same Wi-Fi):
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do for /f "tokens=*" %%b in ("%%a") do echo    http://%%b:8000
echo.
venv\Scripts\python manage.py runserver 0.0.0.0:8000
