"""Ingesta de titulares financieros via RSS publico (gratis, sin scraping de
HTML fragil ni API de pago). Deliberadamente NO calcula sentimiento aqui: eso
es una feature derivada y vive en features/news_features.py, para que el
punto en el que "informacion cruda" se convierte en "señal" quede en una sola
capa auditable.

Fuentes configuradas en config.yaml (news.rss_sources). RSS es contenido
pensado para sindicacion publica, a diferencia de raspar el HTML de la web.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import feedparser
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

NEWS_COLUMNS = ["ts", "headline", "summary", "source", "url"]


class NewsProviderError(RuntimeError):
    pass


class RSSNewsProvider:
    name = "rss_news"

    def __init__(self, sources: list[dict]):
        # sources: [{"name": "investing_forex", "url": "https://..."}, ...]
        self._sources = sources

    def is_available(self) -> bool:
        if not self._sources:
            return False
        try:
            parsed = feedparser.parse(self._sources[0]["url"])
            return getattr(parsed, "bozo", 1) == 0 or len(parsed.entries) > 0
        except Exception:
            return False

    def fetch_headlines(self) -> pd.DataFrame:
        rows: list[dict] = []
        failures: list[str] = []

        for source in self._sources:
            try:
                rows.extend(self._fetch_one(source["name"], source["url"]))
            except Exception as exc:  # noqa: BLE001 - una fuente caida no debe tumbar las demas
                failures.append(source["name"])
                logger.warning("RSS '%s' fallo: %s", source["name"], exc)

        if failures and len(failures) == len(self._sources):
            raise NewsProviderError(f"todas las fuentes RSS fallaron: {failures}")

        df = pd.DataFrame(rows, columns=NEWS_COLUMNS)
        if not df.empty:
            df = df.drop_duplicates(subset="url").sort_values("ts").reset_index(drop=True)
        return df

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=3, max=20))
    def _fetch_one(self, source_name: str, url: str) -> list[dict]:
        parsed = feedparser.parse(url)
        if getattr(parsed, "bozo", 0) == 1 and not parsed.entries:
            raise NewsProviderError(f"feed invalido o inalcanzable: {url}")

        rows = []
        for entry in parsed.entries:
            ts = self._entry_ts(entry)
            if ts is None:
                continue
            rows.append({
                "ts": ts,
                "headline": entry.get("title", "").strip(),
                "summary": entry.get("summary", "").strip(),
                "source": source_name,
                "url": entry.get("link", ""),
            })
        return rows

    @staticmethod
    def _entry_ts(entry) -> datetime | None:
        for field in ("published_parsed", "updated_parsed"):
            value = entry.get(field)
            if value:
                year, month, day, hour, minute, second = value[:6]
                return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
        # Sin fecha de publicacion no podemos garantizar point-in-time: se
        # descarta en vez de asumir "ahora", que falsearia el historial.
        logger.warning("entrada RSS sin fecha de publicacion, se ignora: %s", entry.get("title"))
        return None
