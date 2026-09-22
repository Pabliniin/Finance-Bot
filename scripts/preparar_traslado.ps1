# Prepara un unico ZIP con TODO lo que necesita otro PC para ejecutar el bot:
# codigo, configuracion, historico descargado, modelo entrenado y el .env con
# tus credenciales. No incluye el entorno virtual (se crea alli) ni los logs.
#
# Uso:  powershell -ExecutionPolicy Bypass -File scripts\preparar_traslado.ps1
# Resultado: FinanceBot-traslado.zip en el Escritorio.
#
# OJO: el ZIP lleva tu .env (token del bot). Trata ese fichero como una llave:
# no lo subas a ningun sitio ni lo dejes en un USB compartido.

param([string]$Destino = "$env:USERPROFILE\Desktop\FinanceBot-traslado.zip")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$temp = Join-Path $env:TEMP ("finance-bot-traslado-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$destinoCarpeta = Join-Path $temp "FinanceBot"
New-Item -ItemType Directory -Path $destinoCarpeta -Force | Out-Null

Write-Host "== Copiando el proyecto (sin .venv, logs ni cache) ==" -ForegroundColor Cyan
$excluir = @(".venv", ".git", "logs", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "reports")
robocopy $root $destinoCarpeta /E /XD $excluir /XF "*.pyc" /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Fallo copiando los ficheros (robocopy $LASTEXITCODE)." }

# Version empaquetada: asi el actualizador automatico sabe donde esta y solo
# baja lo que sea mas nuevo que esto
$sha = (git -C $root rev-parse HEAD).Trim()
New-Item -ItemType Directory -Path (Join-Path $destinoCarpeta "data") -Force | Out-Null
$estado = @{ sha = $sha; packaged_at = (Get-Date).ToUniversalTime().ToString("o"); failed_boots = 0; previous = $false }
$estado | ConvertTo-Json | Set-Content -Path (Join-Path $destinoCarpeta "data\update_state.json") -Encoding utf8

# Los dos lanzadores, bien visibles en la raiz
Copy-Item (Join-Path $root "scripts\INSTALAR.bat") (Join-Path $destinoCarpeta "INSTALAR.bat") -Force
Copy-Item (Join-Path $root "scripts\ACTUALIZAR.bat") (Join-Path $destinoCarpeta "ACTUALIZAR.bat") -Force

$env_ok = Test-Path (Join-Path $destinoCarpeta ".env")
$datos = (Get-ChildItem (Join-Path $destinoCarpeta "data") -Recurse -File -ErrorAction SilentlyContinue |
    Measure-Object Length -Sum).Sum / 1MB
$modelo = Test-Path (Join-Path $destinoCarpeta "models\validation.json")

Write-Host "== Comprimiendo ==" -ForegroundColor Cyan
if (Test-Path $Destino) { Remove-Item $Destino -Force }
Compress-Archive -Path $destinoCarpeta -DestinationPath $Destino -CompressionLevel Optimal
Remove-Item $temp -Recurse -Force

$tam = [math]::Round((Get-Item $Destino).Length / 1MB, 1)
Write-Host ""
Write-Host "Listo: $Destino  ($tam MB)" -ForegroundColor Green
Write-Host ("  .env incluido:      " + $(if ($env_ok) { "si" } else { "NO (tendras que crearlo alli)" }))
Write-Host ("  historico incluido: " + [math]::Round($datos, 1) + " MB")
Write-Host ("  modelo entrenado:   " + $(if ($modelo) { "si" } else { "NO" }))
Write-Host ""
Write-Host "En el PC nuevo: copia el ZIP, extraelo y doble clic en INSTALAR.bat" -ForegroundColor Cyan
