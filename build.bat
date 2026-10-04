@echo off
setlocal
py -m pip install -r requirements.txt
if errorlevel 1 exit /b %errorlevel%
py -m PyInstaller --noconfirm --clean --windowed --onefile --name ScreenMarker ScreenMarker.py
if errorlevel 1 exit /b %errorlevel%
echo.
echo Created dist\ScreenMarker.exe
pause