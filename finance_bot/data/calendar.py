"""Calendario economico gratuito: feed JSON semanal de ForexFactory
(nfs.faireconomy.media), el que usan muchas herramientas open source.

Limitaciones asumidas con presupuesto 0: es un feed no oficial (puede cambiar
o caer sin aviso) y solo cubre esta semana y la siguiente. Si falla, el bot
lo dice en /estado y en cada señal ("calendario no disponible"); nunca
inventa que "no hay eventos".
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pandas as pd
import requests

logger = logging.getLogger(__name__)

FEEDS = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
)
CACHE_SECONDS = 3600


@dataclass(frozen=True)
class EconomicEvent:
    time: datetime
    currency: str
    title: str
    impact: str
    forecast: str
    previous: str


class EconomicCalendar:
    def __init__(self) -> None:
        self._events: list[EconomicEvent] = []
        self._fetched_at = 0.0
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        return bool(self._events) or self.last_error is None

    def refresh(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._fetched_at < CACHE_SECONDS:
            return
        events: list[EconomicEvent] = []
        errors = []
        for url in FEEDS:
            try:
                response = requests.get(url, timeout=20, headers={"User-Agent": "finance-bot/2.0"})
                if response.status_code == 404:
                    continue  # la semana siguiente a veces aun no esta publicada
                response.raise_for_status()
                for raw in response.json():
                    try:
                        when = pd.Timestamp(raw["date"]).tz_convert("UTC").to_pydatetime()
                    except (KeyError, ValueError, TypeError):
                        continue
                    events.append(
                        EconomicEvent(
                            time=when,
                            currency=str(raw.get("country", "")).upper(),
                            title=str(raw.get("title", "")).strip(),
                            impact=str(raw.get("impact", "")).strip(),
                            forecast=str(raw.get("forecast", "") or ""),
                            previous=str(raw.get("previous", "") or ""),
                        )
                    )
            except (requests.RequestException, ValueError) as exc:
                errors.append(f"{url}: {exc}")
        if events or not errors:
            self._events = sorted(set(events), key=lambda e: e.time)
            self.last_error = None
        else:
            self.last_error = "; ".join(errors)
            logger.warning("Calendario no disponible: %s", self.last_error)
        self._fetched_at = time.monotonic()

    def upcoming(
        self, currencies: list[str], hours: float, impact: tuple[str, ...] = ("High",), now: datetime | None = None
    ) -> list[EconomicEvent]:
        now = now or datetime.now(UTC)
        until = now + timedelta(hours=hours)
        return [e for e in self._events if e.currency in currencies and e.impact in impact and now <= e.time <= until]
