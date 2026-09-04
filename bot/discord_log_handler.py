"""Handler de logging que reenvia registros ERROR/CRITICAL al canal de
alertas de Discord, ademas del fichero/consola habituales (requisito
explicito: "logging estructurado a fichero y a canal de Discord para errores
criticos"). Se añade a los procesos desatendidos (scheduler/run.py,
bot/main.py) - un fallo aqui NUNCA debe propagar una excepcion nueva dentro
del propio sistema de logging, asi que cualquier error al enviar se traga en
silencio (se pierde la alerta puntual, no se cae el proceso por ello).
"""

from __future__ import annotations

import asyncio
import logging

from bot.notifier import send_text
from config.settings import get_settings


class DiscordAlertHandler(logging.Handler):
    def __init__(self, channel_id: str, level: int = logging.ERROR):
        super().__init__(level=level)
        self._channel_id = channel_id

    def emit(self, record: logging.LogRecord) -> None:
        if not self._channel_id:
            return
        try:
            message = self.format(record)
            asyncio.run(send_text(self._channel_id, f"🚨 **Error critico**\n```{message[:1900]}```"))
        except Exception:  # noqa: BLE001 - un fallo enviando la alerta no puede tumbar el logging
            pass


def install(logger: logging.Logger | None = None) -> None:
    settings = get_settings()
    if not settings.discord_alerts_channel_id:
        return
    handler = DiscordAlertHandler(settings.discord_alerts_channel_id)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    (logger or logging.getLogger()).addHandler(handler)
