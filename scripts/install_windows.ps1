# Instalacion en el PC Windows donde corre MetaTrader 5.
# Uso (PowerShell, dentro de la carpeta del proyecto):
#     powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
# Opcional: -RegisterTask para arrancar el bot automaticamente al iniciar sesion.

param([switch]$RegisterTask)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== 1/4 Comprobando Python ==" -ForegroundColor Cyan
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw "No se encuentra Python. Instala Python 3.12 desde python.org (marca 'Add python.exe to PATH')." }
$version = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([version]$version -lt [version]"3.11") { throw "Python $version es demasiado antiguo: necesitas 3.11 o superior." }
Write-Host "Python $version OK"

Write-Host "== 2/4 Entorno virtual y dependencias ==" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { & python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -e ".[mt5]" --quiet
if ($LASTEXITCODE -ne 0) { throw "Fallo instalando dependencias." }
Write-Host "Dependencias instaladas"

Write-Host "== 3/4 Configuracion ==" -ForegroundColor Cyan
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Creado .env: abrelo y pega tu DISCORD_BOT_TOKEN (lo demas es opcional)." -ForegroundColor Yellow
} else {
    Write-Host ".env ya existe (no se toca)."
}

Write-Host "== 4/4 Datos y modelo ==" -ForegroundColor Cyan
if (-not (Test-Path "models\validation.json")) {
    Write-Host "No hay modelo entrenado. Ejecuta (tarda, el servidor de datos limita la velocidad):" -ForegroundColor Yellow
    Write-Host "    .\.venv\Scripts\python.exe -m finance_bot download"
    Write-Host "    .\.venv\Scripts\python.exe -m finance_bot research"
} else {
    Write-Host "Modelo encontrado en models\ (copiado del PC de desarrollo o ya entrenado)."
}

if ($RegisterTask) {
    $action = New-ScheduledTaskAction -Execute "$root\.venv\Scripts\pythonw.exe" -Argument "-m finance_bot run" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName "FinanceBot" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Host "Tarea 'FinanceBot' registrada: el bot arrancara al iniciar sesion y se reiniciara si se cae." -ForegroundColor Green
}

Write-Host ""
Write-Host "== Que falta para tenerlo funcionando ==" -ForegroundColor Green
Write-Host "  1. Pega tu token en .env  (DISCORD_BOT_TOKEN=...)"
Write-Host "  2. .\.venv\Scripts\python.exe -m finance_bot invitar     # enlace para meterlo en tu servidor"
Write-Host "  3. .\.venv\Scripts\python.exe -m finance_bot check       # comprueba datos, modelo y MT5"
Write-Host "  4. .\.venv\Scripts\python.exe -m finance_bot run         # arranca el bot"
Write-Host ""
Write-Host "Para que arranque solo al encender el PC:" -ForegroundColor Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1 -RegisterTask"
