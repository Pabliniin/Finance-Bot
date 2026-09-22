@echo off
REM ===========================================================================
REM  Finance Bot - instalacion en un PC nuevo (el que va a quedarse encendido)
REM  Doble clic y listo: instala dependencias, registra el arranque automatico
REM  y deja el bot funcionando.
REM ===========================================================================
cd /d "%~dp0"
if exist "scripts\install_windows.ps1" goto raiz
cd ..
:raiz

echo.
echo === Instalando Finance Bot en este PC ===
echo.
powershell -ExecutionPolicy Bypass -File "scripts\install_windows.ps1" -RegisterTask
if errorlevel 1 goto error

echo.
echo === Arrancando el bot ===
powershell -ExecutionPolicy Bypass -Command "Start-ScheduledTask -TaskName 'FinanceBot'; Start-Sleep -Seconds 15; Get-ScheduledTask -TaskName 'FinanceBot' | Select-Object TaskName,State | Format-Table -AutoSize"

echo.
echo === Comprobacion ===
".venv\Scripts\python.exe" -m finance_bot check

echo.
echo ---------------------------------------------------------------------------
echo  Si arriba pone "Fuente en vivo: MT5 OK" ya esta todo.
echo  Si pone Dukascopy, instala MetaTrader 5, entra UNA vez en tu cuenta demo
echo  marcando "Guardar datos de la cuenta" y en 10 minutos el bot se pasa solo
echo  a tiempo real (no hace falta reiniciar nada).
echo ---------------------------------------------------------------------------
echo.
pause
exit /b 0

:error
echo.
echo Algo ha fallado en la instalacion. Revisa los mensajes de arriba.
pause
exit /b 1
