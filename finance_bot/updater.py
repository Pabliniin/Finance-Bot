"""Actualizacion automatica desde GitHub.

El bot vive en un PC que nadie mira. Cuando hay un arreglo en el repositorio,
esto lo baja, lo aplica y sale con un codigo especial: la tarea programada lo
vuelve a arrancar con el codigo nuevo. Datos, modelo, base de datos y .env no
se tocan nunca.

Seguridad de la operacion:
- solo se aplica codigo de la rama `main` del repositorio configurado;
- antes de sobrescribir se guarda una copia del codigo anterior;
- si tras actualizar el bot no consigue arrancar 3 veces seguidas, se vuelve
  a la copia anterior (`boot_guard`), asi un despliegue roto no lo deja muerto.
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import requests

from finance_bot.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

REPO = "Pabliniin/Finance-Bot"
BRANCH = "main"
API_LATEST = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
ZIP_URL = f"https://codeload.github.com/{REPO}/zip/{{sha}}"
# Lo que se actualiza. Todo lo demas (data/, models/, logs/, .env, .venv) se conserva.
CODE_PATHS = ("finance_bot", "config/settings.yaml", "scripts", "docs", "pyproject.toml", "README.md")
STATE_FILE = PROJECT_ROOT / "data" / "update_state.json"
BACKUP_DIR = PROJECT_ROOT / ".update_backup"
EXIT_RESTART = 3  # la tarea programada reinicia el proceso al salir con error
MAX_FAILED_BOOTS = 3


def _read_state() -> dict:
    try:
        # utf-8-sig: el fichero puede venir de PowerShell (preparar_traslado.ps1), que escribe BOM
        return json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")


def current_sha() -> str | None:
    state = _read_state()
    if state.get("sha"):
        return str(state["sha"])
    try:  # instalacion desde git (PC de desarrollo)
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10, check=False
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def latest_sha(timeout: int = 20) -> str | None:
    try:
        response = requests.get(API_LATEST, timeout=timeout, headers={"Accept": "application/vnd.github+json"})
        response.raise_for_status()
        return str(response.json()["sha"])
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.warning("No se pudo consultar GitHub: %s", type(exc).__name__)
        return None


def is_developer_install() -> bool:
    """Con un .git en la carpeta, el codigo lo gobierna git (PC de desarrollo):
    sobrescribirlo desde GitHub pisaria trabajo sin subir."""
    return (PROJECT_ROOT / ".git").exists()


def update_available(force: bool = False) -> str | None:
    """SHA nuevo si el repositorio tiene algo que no tenemos; None si estamos al
    dia o si esta instalacion la gobierna git."""
    if is_developer_install() and not force:
        return None
    latest = latest_sha()
    return latest if latest and latest != current_sha() else None


def _copy_tree(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def apply_update(sha: str) -> None:
    """Descarga el codigo de ese commit y lo pone en su sitio, guardando antes
    una copia de lo que habia. Lanza si algo falla; en ese caso no se ha
    sobrescrito nada."""
    logger.info("Descargando la version %s", sha[:10])
    response = requests.get(ZIP_URL.format(sha=sha), timeout=120)
    response.raise_for_status()
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    root_name = archive.namelist()[0].split("/")[0]
    staging = PROJECT_ROOT / ".update_staging"
    shutil.rmtree(staging, ignore_errors=True)
    archive.extractall(staging)
    source = staging / root_name
    if not (source / "finance_bot" / "__main__.py").exists():
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError("el paquete descargado no tiene el aspecto esperado; no se aplica")

    shutil.rmtree(BACKUP_DIR, ignore_errors=True)
    for rel in CODE_PATHS:
        if (PROJECT_ROOT / rel).exists():
            _copy_tree(PROJECT_ROOT / rel, BACKUP_DIR / rel)
    for rel in CODE_PATHS:
        if (source / rel).exists():
            _copy_tree(source / rel, PROJECT_ROOT / rel)
    shutil.rmtree(staging, ignore_errors=True)

    # dependencias nuevas, si las hubiera (rapido si no cambio nada)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"],
        cwd=PROJECT_ROOT,
        check=False,
        timeout=600,
    )
    _write_state({"sha": sha, "updated_at": datetime.now(UTC).isoformat(), "failed_boots": 0, "previous": True})
    logger.info("Codigo actualizado a %s; hace falta reiniciar", sha[:10])


def rollback() -> bool:
    if not BACKUP_DIR.exists():
        return False
    for rel in CODE_PATHS:
        if (BACKUP_DIR / rel).exists():
            _copy_tree(BACKUP_DIR / rel, PROJECT_ROOT / rel)
    state = _read_state()
    state.update({"sha": None, "failed_boots": 0, "previous": False, "rolled_back_at": datetime.now(UTC).isoformat()})
    _write_state(state)
    logger.error("Actualizacion revertida: el bot no arrancaba con el codigo nuevo")
    return True


def boot_guard_start() -> None:
    """Se llama al arrancar: cuenta el intento. Si una actualizacion reciente
    lleva varios arranques fallidos, se vuelve al codigo anterior."""
    state = _read_state()
    if not state.get("previous"):
        return
    state["failed_boots"] = int(state.get("failed_boots", 0)) + 1
    if state["failed_boots"] >= MAX_FAILED_BOOTS:  # al tercer arranque fallido, se revierte
        rollback()
        return
    _write_state(state)


def boot_guard_ok() -> None:
    """Se llama cuando el bot esta listo: el arranque fue bien."""
    state = _read_state()
    if state.get("failed_boots"):
        state["failed_boots"] = 0
        _write_state(state)


def check_and_apply() -> bool:
    """Devuelve True si se aplico una actualizacion (y hay que reiniciar)."""
    sha = update_available()
    if not sha:
        return False
    try:
        apply_update(sha)
    except (requests.RequestException, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        logger.error("La actualizacion fallo y no se aplico: %s", exc)
        return False
    return True


TASK_NAME = "FinanceBot"


def schedule_restart() -> None:
    """Deja programado que la tarea vuelva a arrancar en unos segundos, ya con
    este proceso muerto. Si el bot no corre bajo la tarea, no pasa nada: el
    comando falla en silencio y quien lo arranco a mano lo vuelve a lanzar."""
    if sys.platform != "win32":
        return
    try:
        subprocess.Popen(
            ["cmd", "/c", f"timeout /t 8 /nobreak >nul && schtasks /run /tn {TASK_NAME}"],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    except OSError:
        logger.warning("no se pudo programar el reinicio; la tarea lo reintentara sola")


def run_update_command() -> int:
    """`python -m finance_bot update`: actualiza a mano (ACTUALIZAR.bat)."""
    from finance_bot.logging_setup import setup_logging

    setup_logging("update")
    current = current_sha()
    sha = update_available()
    if not sha:
        print(f"Ya estas en la ultima version ({(current or 'desconocida')[:10]}).")
        return 0
    print(f"Actualizando {(current or 'desconocida')[:10]} -> {sha[:10]} …")
    try:
        apply_update(sha)
    except (requests.RequestException, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"No se pudo actualizar: {exc}")
        return 1
    print("Listo. Reinicia el bot (la tarea programada lo hace sola si estaba en marcha).")
    return 0
