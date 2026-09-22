@echo off
REM Arranca el bot de Discord en una ventana visible (doble clic).
REM Para que arranque solo al iniciar sesion: scripts\install_windows.ps1 -RegisterTask
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m finance_bot run
pause
