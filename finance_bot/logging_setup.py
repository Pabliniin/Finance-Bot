from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from finance_bot.config import PROJECT_ROOT


def setup_logging(name: str, level: int = logging.INFO) -> None:
    """Consola + fichero rotativo en logs/<name>.log (5 x 2 MB)."""
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(log_dir / f"{name}.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # httpx (usado por discord.py) registra cada peticion en INFO,
    # incluida la URL con el token del bot. Nunca debe acabar en un log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
