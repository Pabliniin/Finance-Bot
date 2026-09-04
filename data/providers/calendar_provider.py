"""Calendario economico gratuito, via el feed JSON comunitario de ForexFactory
(nfs.faireconomy.media), el mismo que usan multiples EAs/herramientas open
source porque ForexFactory no ofrece una API oficial gratuita.

Limitaciones que hay que asumir con presupuesto 0e:
- Es un feed NO oficial: puede cambiar de forma o desaparecer sin aviso. Por
  eso is_available() se usa activamente en /estado y el pipeline debe saber
  degradar (avisar, no inventar eventos) si el feed falla.
- Sin control de rate limit publicado: se pide con moderacion (una vez al dia
  en el job diario, nunca en un loop ajustado) y con backoff ante fallos.
- Los nombres de campo del JSON no estan documentados formalmente y han
  cambiado en el pasado entre snapshots de la comunidad. El parseo de abajo es
  defensivo (usa .get) pero AUN ASI conviene, la primera vez que se ejecute de
  verdad, volcar un ejemplo crudo y confirmar que los campos siguen llamandose
  igual antes de confiar en produccion.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_BASE_URL = "https://nfs.faireconomy.media/ff_calendar_{period}.json"
_IMPACT_MAP = {"high": "high", "medium": "medium", "low": "low", "non-economic": "low", "holiday": "low"}

CALENDAR_COLUMNS = [
    "event_id", "ts", "country", "currency", "event_name", "impact",
    "actual", "forecast", "previous", "source",
]


class CalendarProviderError(RuntimeError):
    pass


class ForexFactoryCalendarProvider:
    name = "forexfactory_json"

    def is_available(self) -> bool:
        try:
            r = requests.get(_BASE_URL.format(period="thisweek"), timeout=10)
            return r.status_code == 200
        except requests.RequestException:
            return False

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
    def _fetch_period(self, period: str) -> list[dict]:
        r = requests.get(_BASE_URL.format(period=period), timeout=15)
        r.raise_for_status()
        return r.json()

    def fetch_calendar(self, lookahead_days: int, lookback_days: int) -> pd.DataFrame:
        periods = ["lastweek", "thisweek", "nextweek"]
        raw_events: list[dict] = []
        for period in periods:
            try:
                raw_events.extend(self._fetch_period(period))
            except Exception as exc:  # noqa: BLE001 - degradacion elegante, no propagamos y rompemos el job
                logger.warning("No se pudo obtener calendario '%s': %s", period, exc)

        if not raw_events:
            raise CalendarProviderError("las tres ventanas del calendario fallaron, no hay datos que devolver")

        now = datetime.now(UTC)
        lo = now - timedelta(days=lookback_days)
        hi = now + timedelta(days=lookahead_days)

        rows = []
        for ev in raw_events:
            ts = self._parse_ts(ev.get("date"))
            if ts is None or not (lo <= ts <= hi):
                continue
            title = ev.get("title", "").strip()
            country = ev.get("country", "").strip()
            event_id = hashlib.sha1(f"{title}|{country}|{ts.isoformat()}".encode()).hexdigest()[:16]
            rows.append({
                "event_id": event_id,
                "ts": ts,
                "country": country,
                "currency": country,  # el feed usa el codigo de pais/divisa indistintamente (p.ej. 'USD')
                "event_name": title,
                "impact": _IMPACT_MAP.get(str(ev.get("impact", "")).lower(), "low"),
                "actual": ev.get("actual"),
                "forecast": ev.get("forecast"),
                "previous": ev.get("previous"),
                "source": self.name,
            })

        return pd.DataFrame(rows, columns=CALENDAR_COLUMNS)

    @staticmethod
    def _parse_ts(raw_date: str | None) -> datetime | None:
        if not raw_date:
            return None
        try:
            ts = pd.Timestamp(raw_date)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            return ts.tz_convert("UTC").to_pydatetime()
        except (ValueError, TypeError):
            logger.warning("timestamp de calendario no parseable: %r", raw_date)
            return None
