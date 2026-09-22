"""Orquestacion: datos en vivo -> seguimiento de señales abiertas -> kill
switch -> analisis -> nuevas señales. Lo usan el bot de Discord y los
comandos de terminal `scan` y `check` (mismo codigo, mismas reglas).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from finance_bot.config import AppConfig, Secrets, load_config, load_secrets
from finance_bot.data.calendar import EconomicCalendar
from finance_bot.data.dukascopy import DukascopyClient, download_h1_history, download_m1_history
from finance_bot.data.live import DukascopyFeed, LiveFeed, LiveFeedError, create_feed
from finance_bot.data.market import MarketData
from finance_bot.engine import exits
from finance_bot.engine.exits import ExitAdvice
from finance_bot.engine.signals import Analysis, Artifacts, Signal, SignalEngine
from finance_bot.tracking import Tracker

logger = logging.getLogger(__name__)

LIVE_LOOKBACK_DAYS = 45
# Un mismo tipo de aviso no se repite antes de esto (salvo que cambie de tipo).
ADVICE_COOLDOWN = timedelta(hours=6)
# Cada cuanto se reintenta MT5 cuando estamos con el respaldo retrasado.
FEED_RETRY = timedelta(minutes=10)


class _Missing:
    """Marca "no me han pasado cotizacion" sin confundirla con "no hay"."""


_MISSING = _Missing()


@dataclass
class ScanResult:
    notice: str | None = None  # aviso de degradacion (no es un fallo: el bot sigue)
    new_signals: list[Signal] = field(default_factory=list)
    advice: list[ExitAdvice] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    kill_switch_reason: str | None = None
    analyses: dict[str, Analysis] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class BotService:
    def __init__(self, cfg: AppConfig | None = None, secrets: Secrets | None = None):
        self.cfg = cfg or load_config()
        self.secrets = secrets or load_secrets()
        self.md = MarketData(self.cfg)
        self.tracker = Tracker(self.cfg)
        self.calendar = EconomicCalendar()
        self.artifacts = Artifacts.load()
        self.engine = SignalEngine(self.cfg, self.md, self.artifacts, self.calendar)
        self.feed: LiveFeed | None = None
        self.complete_until: dict[str, pd.Timestamp] = {}
        self.latest_analyses: dict[str, Analysis] = {}
        self.last_scan: datetime | None = None
        self.last_error: str | None = None
        self._feed_checked = datetime.now(UTC)
        self._lock = threading.Lock()  # un escaneo a la vez (MT5 no es seguro entre hilos)

    # --- datos --------------------------------------------------------------------

    def _ensure_feed(self) -> LiveFeed:
        """Si al arrancar no habia MT5 (el PC acaba de encenderse, el terminal
        tarda en estar listo), se reintenta cada rato: si no, el bot se quedaria
        con datos de una hora de retraso hasta el siguiente reinicio."""
        now = datetime.now(UTC)
        if self.feed is None:
            self.feed = create_feed(self.cfg, self.secrets)
            self._feed_checked = now
            logger.info("Fuente de datos en vivo: %s", self.feed.name)
        elif not self.feed.realtime and now - self._feed_checked >= FEED_RETRY:
            self._feed_checked = now
            candidate = create_feed(self.cfg, self.secrets)
            if candidate.realtime:
                logger.info("Fuente en vivo mejorada a %s (antes %s)", candidate.name, self.feed.name)
                self.feed = candidate
        return self.feed

    def refresh_data(self) -> None:
        """Trae las velas M1 que falten. Garantiza CONTINUIDAD: el M1 debe cubrir
        desde donde acaba el historico H1 mensual (y al menos las ultimas
        semanas para M15); un hueco ahi haria calcular H4/D1 con velas que faltan."""
        feed = self._ensure_feed()
        now = pd.Timestamp.now(tz="UTC")
        floor = now - pd.Timedelta(days=LIVE_LOOKBACK_DAYS)
        for symbol in self.cfg.instruments:
            h1_last = self.md.h1_store.last_timestamp(symbol)
            need_from = floor if h1_last is None else min(h1_last + pd.Timedelta(hours=1), floor)
            m1_last = self.md.m1_store.last_timestamp(symbol)
            since = max(m1_last + pd.Timedelta(minutes=1), need_from) if m1_last is not None else need_from
            bars, complete_until = feed.fetch_m1(symbol, since.to_pydatetime())
            self.md.m1_store.write(symbol, bars)
            self.complete_until[symbol] = complete_until

    def refresh_with_fallback(self) -> str | None:
        """Si la fuente en vivo falla (MT5 cerrado, terminal reiniciandose), se
        sigue con el respaldo retrasado en vez de dejar al bot a ciegas. Con
        datos retrasados el propio filtro descarta las señales por tardias, asi
        que no se emite nada dudoso. Devuelve el aviso, si lo hubo."""
        try:
            self.refresh_data()
            return None
        except (LiveFeedError, OSError) as exc:
            if self.feed is None or not self.feed.realtime:
                raise
            logger.warning("Fuente en vivo caida (%s); paso al respaldo con retraso", exc)
            self.feed = DukascopyFeed(self.cfg)
            self._feed_checked = datetime.now(UTC)
            self.refresh_data()
            return f"MT5 no responde ({exc}); mientras tanto, datos con retraso"

    def maintenance(self) -> str:
        """Diario: consolida el historico con Dukascopy (meses H1 cerrados y
        dias M1 completos). Reanudable y barato si ya esta al dia."""
        client = DukascopyClient()
        lines = []
        for symbol in self.cfg.instruments:
            divisor = self.cfg.instrument(symbol).dukascopy_divisor
            n_h1 = download_h1_history(
                client, self.md.h1_store, symbol, divisor, date.fromisoformat(self.cfg.data.history_start)
            )
            m1_from = max(
                date.fromisoformat(self.cfg.data.m1_history_start),
                datetime.now(UTC).date() - timedelta(days=LIVE_LOOKBACK_DAYS),
            )
            n_m1 = download_m1_history(client, self.md.m1_store, symbol, divisor, m1_from)
            lines.append(f"{symbol}: +{n_h1} velas H1, +{n_m1} velas M1")
        return "; ".join(lines)

    # --- escaneo ------------------------------------------------------------------

    def analyze(self, symbol: str, quote: tuple[float, float] | None | _Missing = _MISSING) -> Analysis:
        feed = self._ensure_feed()
        return self.engine.analyze(
            symbol,
            complete_until=self.complete_until.get(symbol),
            quote=feed.quote(symbol) if isinstance(quote, _Missing) else quote,
            feed_name=feed.name,
            realtime=feed.realtime,
            capital_eur=self.tracker.capital_eur(),
            risk_pct=self.tracker.risk_pct(),
            mode=self.tracker.mode(),
        )

    def scan(self) -> ScanResult:
        with self._lock:
            result = ScanResult()
            try:
                result.notice = self.refresh_with_fallback()
            except (LiveFeedError, OSError) as exc:
                result.errors.append(f"datos en vivo: {exc}")
                self.last_error = str(exc)
                return result
            self.calendar.refresh()

            result.events = self.tracker.update_open(self.md)
            for event in result.events:
                if event["type"] == "closed":
                    # honestidad: si hubo aviso, que se vea que habria pasado haciendole caso
                    event["advice_r"], event["advice_headline"] = self.tracker.first_advice_r(event["key"])
            result.kill_switch_reason = self.tracker.evaluate_kill_switch()
            kill_active, _ = self.tracker.kill_switch_status()
            open_now = len(self.tracker.open_signals())
            now = datetime.now(UTC)

            quotes: dict[str, tuple[float, float] | None] = {}
            for symbol in self.cfg.instruments:
                try:
                    quotes[symbol] = feed.quote(symbol) if (feed := self.feed) else None
                except Exception:  # noqa: BLE001 - sin cotizacion se sigue con el ultimo cierre
                    logger.warning("sin cotizacion de %s", symbol)
                    quotes[symbol] = None

            for symbol in self.cfg.instruments:
                try:
                    analysis = self.analyze(symbol, quote=quotes[symbol])
                except Exception as exc:  # noqa: BLE001 - un instrumento roto no debe tumbar el otro
                    logger.exception("analisis de %s fallo", symbol)
                    result.errors.append(f"{symbol}: {exc}")
                    continue
                result.analyses[symbol] = analysis
                muted = self.tracker.muted_until(symbol)
                for signal in analysis.signals:
                    if not signal.emit or self.tracker.is_known(signal.key):
                        continue
                    if kill_active:
                        signal.blockers.append("interruptor de seguridad activo")
                        continue
                    if muted and muted > now:
                        continue
                    if open_now >= self.cfg.account.max_open_signals:
                        signal.blockers.append(
                            f"ya hay {open_now} señales abiertas (maximo {self.cfg.account.max_open_signals})"
                        )
                        continue
                    self.tracker.record(signal)
                    result.new_signals.append(signal)
                    open_now += 1
            result.advice = self._exit_advice(result.analyses, quotes, now)
            self.last_scan = now
            self.latest_analyses = result.analyses
            self.last_error = "; ".join(result.errors) or None
            return result

    def _exit_advice(
        self, analyses: dict[str, Analysis], quotes: dict[str, tuple[float, float] | None], now: datetime
    ) -> list[ExitAdvice]:
        """Revisa las señales abiertas contra el analisis recien hecho. Un mismo
        tipo de aviso no se repite antes de ADVICE_COOLDOWN."""
        out: list[ExitAdvice] = []
        for _, signal in self.tracker.open_signals().iterrows():
            symbol = str(signal["symbol"])
            analysis = analyses.get(symbol)
            quote = quotes.get(symbol)
            current = exits.r_now(float(signal["entry"]), float(signal["r_price"]), int(signal["direction"]), quote)
            peak = self.tracker.update_peak_r(str(signal["key"]), current) if current is not None else None
            view = analysis.views.get(str(signal["tf"])) if analysis else None
            news = analysis.news if analysis else []
            for advice in exits.evaluate(signal, view, news, quote, self.cfg, now, peak):
                last = self.tracker.last_advice_at(advice.key, advice.kind)
                if last is not None and now - last < ADVICE_COOLDOWN:
                    continue
                self.tracker.record_advice(advice)
                out.append(advice)
        return out

    def health(self) -> dict:
        info: dict = {
            "feed": self.feed.name if self.feed else "sin conectar",
            "last_scan": self.last_scan,
            "last_error": self.last_error,
            "model": None,
            "data": {},
            "calendar_ok": self.calendar.last_error is None,
            "kill_switch": self.tracker.kill_switch_status(),
            "mode": self.tracker.mode(),
            "open_signals": len(self.tracker.open_signals()),
        }
        if self.artifacts:
            first = next(iter(self.artifacts.models.values()))
            info["model"] = {
                "trained_at": first.metadata.get("trained_at"),
                "test_evaluated": self.artifacts.validation.get("test_evaluated"),
                "selection": self.artifacts.validation.get("selection", {}),
            }
        for symbol in self.cfg.instruments:
            info["data"][symbol] = {
                "h1_until": self.md.h1_store.last_timestamp(symbol),
                "m1_until": self.md.m1_store.last_timestamp(symbol),
                "complete_until": self.complete_until.get(symbol),
            }
        return info


def run_single_scan() -> int:
    """Un escaneo completo ahora mismo, impreso en la terminal (sin Discord).
    Registra y sigue las señales igual que el bot: es el mismo codigo."""
    from finance_bot.discord_bot import embeds
    from finance_bot.logging_setup import setup_logging

    setup_logging("scan")
    service = BotService()
    result = service.scan()
    for error in result.errors:
        print(f"ERROR: {error}")
    for analysis in result.analyses.values():
        print("=" * 60)
        print(embeds.to_text(embeds.analysis_embed(analysis, service.cfg)))
    for signal in result.new_signals:
        print("=" * 60)
        print(embeds.to_text(embeds.signal_embed(signal, service.cfg)))
    for advice in result.advice:
        print("=" * 60)
        print(embeds.to_text(embeds.advice_embed(advice, service.cfg)))
    for event in result.events:
        print("=" * 60)
        print(embeds.to_text(embeds.event_embed(event, service.cfg)))
    if not result.new_signals:
        print("\n" + "Ningun setup cumple ahora mismo todas las condiciones para emitir señal.")
    return 0


def run_health_check() -> int:
    from finance_bot.logging_setup import setup_logging

    setup_logging("check")
    service = BotService()
    secrets = service.secrets
    print(f"Token de Discord: {'configurado' if secrets.discord_bot_token else 'FALTA (DISCORD_BOT_TOKEN en .env)'}")
    guild = secrets.discord_guild_id or "sin fijar (usara el servidor donde este al arrancar)"
    print(f"Servidor de Discord: {guild}")
    channel = secrets.discord_channel_id or "sin fijar (elegira el primer canal donde pueda escribir)"
    print(f"Canal de Discord: {channel}")
    print(f"Administradores del bot: {len(secrets.admin_user_ids) or 'todos los del servidor'}")
    print(f"Modelo entrenado: {'si' if service.artifacts else 'NO (python -m finance_bot research)'}")
    for symbol in service.cfg.instruments:
        h1_until = service.md.h1_store.last_timestamp(symbol)
        m1_until = service.md.m1_store.last_timestamp(symbol)
        print(f"{symbol}: H1 hasta {h1_until}, M1 hasta {m1_until}")
    try:
        service.refresh_data()
        feed_name = service.feed.name if service.feed else "?"
        print(f"Fuente en vivo: {feed_name} OK; datos completos hasta {service.complete_until}")
    except (LiveFeedError, OSError) as exc:
        print(f"Fuente en vivo: ERROR {exc}")
    service.calendar.refresh(force=True)
    calendar_error = service.calendar.last_error
    print(f"Calendario: {'OK' if calendar_error is None else f'ERROR {calendar_error}'}")
    return 0
