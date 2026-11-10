@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
where py >nul 2>nul
if errorlevel 1 goto plain
py -3 -m venv .venv
if errorlevel 1 goto fail
goto install
:plain
python -m venv .venv
if errorlevel 1 goto fail
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo Installation complete. Open run_windows.bat.
pause
exit /b 0
:fail
echo Installation failed. Install Python 3.10 or later with Tcl/Tk and check the messages above.
pause
exit /b 1
