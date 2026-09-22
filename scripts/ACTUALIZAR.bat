@echo off
REM Baja la ultima version del bot desde GitHub, la aplica y reinicia el bot.
REM Datos, modelo, base de datos y .env se conservan. Doble clic y listo.
cd /d "%~dp0"
if exist "scripts\install_windows.ps1" goto raiz
cd ..
:raiz
echo === Actualizando Finance Bot ===
".venv\Scripts\python.exe" -m finance_bot update
echo.
echo === Reiniciando el bot ===
schtasks /end /tn FinanceBot >nul 2>&1
timeout /t 3 /nobreak >nul
schtasks /run /tn FinanceBot
timeout /t 20 /nobreak >nul
".venv\Scripts\python.exe" -m finance_bot check
pause
