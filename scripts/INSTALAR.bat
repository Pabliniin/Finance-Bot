@echo off
REM ===========================================================================
REM  Finance Bot - instalacion en un PC nuevo (el que va a quedarse encendido)
REM  Doble clic y listo: instala Python si falta, las dependencias, registra el
REM  arranque automatico, evita que el PC se suspenda y deja el bot funcionando.
REM ===========================================================================
cd /d "%~dp0"
if exist "scripts\install_windows.ps1" goto raiz
cd ..
:raiz

echo.
echo === 1/4 Python ===
where python >nul 2>&1
if %errorlevel%==0 goto python_ok
echo No hay Python. Lo instalo con winget (viene con Windows 10/11)...
winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
REM la sesion actual no ve el PATH nuevo: se añade a mano para este proceso
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
where python >nul 2>&1
if not %errorlevel%==0 (
    echo No he podido instalar Python solo. Instalalo desde python.org marcando "Add python.exe to PATH" y vuelve a hacer doble clic aqui.
    pause
    exit /b 1
)
:python_ok
python --version

echo.
echo === 2/4 Instalando Finance Bot ===
REM si ya habia un bot corriendo (reinstalacion), se para: si no, seguiria vivo con el codigo viejo
schtasks /end /tn FinanceBot >nul 2>&1
taskkill /f /im pythonw.exe >nul 2>&1
powershell -ExecutionPolicy Bypass -File "scripts\install_windows.ps1" -RegisterTask
if errorlevel 1 goto error

echo.
echo === 3/4 Que el PC no se duerma (el bot vigila el mercado las 24 h) ===
powercfg /change standby-timeout-ac 0 >nul 2>&1
powercfg /change hibernate-timeout-ac 0 >nul 2>&1
echo Suspension e hibernacion desactivadas mientras el PC esta enchufado.

echo.
echo === 4/4 Arrancando el bot ===
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
echo.
echo  El bot arranca al iniciar sesion en Windows: deja este usuario con la
echo  sesion iniciada. Si quieres que entre solo al encender el PC, activa el
echo  inicio de sesion automatico (netplwiz) o dimelo y te lo explico.
echo ---------------------------------------------------------------------------
echo.
pause
exit /b 0

:error
echo.
echo Algo ha fallado en la instalacion. Revisa los mensajes de arriba.
pause
exit /b 1
