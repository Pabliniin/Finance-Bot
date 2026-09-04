"""Aplica a mano los ficheros SQL de data/storage/migrations/, en orden, contra
DATABASE_URL. Necesario solo cuando la base ya existe (docker-entrypoint-initdb.d
de Postgres solo corre automaticamente en un volumen vacio la primera vez).

Uso:
    python -m data.storage.migrate
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from sqlalchemy import text

from config.settings import get_settings
from data.storage.db import get_engine

logger = logging.getLogger("data.storage.migrate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run() -> None:
    settings = get_settings()
    engine = get_engine(settings.database_url)

    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        logger.warning("no hay ficheros de migracion en %s", _MIGRATIONS_DIR)
        return

    for path in files:
        logger.info("aplicando %s", path.name)
        sql = path.read_text(encoding="utf-8")
        with engine.begin() as conn:
            conn.execute(text(sql))

    logger.info("migraciones aplicadas: %d ficheros", len(files))


if __name__ == "__main__":
    run()
    sys.exit(0)
