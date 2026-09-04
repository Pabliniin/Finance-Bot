"""Pipeline diario completo: calendario+noticias -> features -> señales de
las 3 estrategias -> filtros de riesgo -> paper trading -> informe a Discord.

NO ingiere velas OHLCV: eso lo hace collector/mt5_live_collector.py, nativo en
el mini PC Windows, que debe correr ANTES de este job (ver
config.yaml:schedule). Este proceso solo lee de Postgres y de fuentes de
noticias/calendario (que si tienen salida a internet dentro de Docker).

Diseño deliberado: TODA señal generada se registra en el ledger (aprobada o
no) antes de saber si el riesgo la aprueba - es lo que permite auditar
despues cuantas señales se descartaron y por que, no solo las que se
ejecutaron.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from backtest.costs import CostModel
from bot.formatting import daily_report_embed, kill_switch_alert_embed, signal_embed
from bot.notifier import send_embed, send_embeds
from config.loader import load_config, load_instruments
from config.settings import get_settings
from data.providers.base import Timeframe
from data.providers.calendar_provider import ForexFactoryCalendarProvider
from data.providers.news_provider import RSSNewsProvider
from data.storage.cache import (
    log_ingestion,
    read_ohlcv,
    set_risk_state,
    upsert_calendar_events,
    upsert_news_headlines,
)
from data.storage.db import get_engine
from features.pipeline import build_feature_matrix
from risk.currency_conversion import CurrencyConversionError, resolve_quote_to_eur_rate
from risk.kill_switch import evaluate_from_equity_curve
from risk.limits import (
    OpenPositionRef,
    PortfolioState,
    check_loss_limits,
    check_new_position_allowed,
    compute_correlation_matrix,
)
from strategy.registry import all_strategies
from tracking.ledger import (
    read_equity_curve,
    read_open_trades_with_signal,
    realized_pnl_eur,
    record_equity,
    record_signal,
    update_signal_status,
)
from tracking.paper_trader import PaperTrader

logger = logging.getLogger("scheduler.daily_job")

_EPOCH = datetime(2020, 1, 1, tzinfo=UTC)


def run() -> dict:
    settings = get_settings()
    cfg = load_config()
    engine = get_engine(settings.database_url)
    instruments = load_instruments()
    now = datetime.now(UTC)

    errors = _ingest_calendar_and_news(engine, cfg)

    capital = cfg.account.capital_reference_eur + realized_pnl_eur(engine, "paper", _EPOCH, now)
    kill_switch_status = _evaluate_kill_switch(engine, cfg, now)

    open_trades = read_open_trades_with_signal(engine, "paper")
    portfolio = PortfolioState(
        open_positions=[
            OpenPositionRef(r.instrument, r.direction, r.risk_eur) for r in open_trades.itertuples()
        ],
        capital_eur=capital,
        daily_pnl_eur=realized_pnl_eur(engine, "paper", now - timedelta(hours=24), now),
        weekly_pnl_eur=realized_pnl_eur(engine, "paper", now - timedelta(days=7), now),
    )
    loss_check = check_loss_limits(portfolio, cfg.account.max_daily_loss_pct, cfg.account.max_weekly_loss_pct)

    can_trade = not kill_switch_status.suspended and loss_check.allowed
    rejections: list[str] = []
    if kill_switch_status.suspended:
        rejections.append(f"TODAS las señales suspendidas por kill switch: {kill_switch_status.reason}")
    elif not loss_check.allowed:
        rejections.append(f"TODAS las señales suspendidas: {loss_check.reason}")

    correlation_matrix = _build_correlation_matrix(engine, instruments, cfg.data.provider_live, now)

    cost_model = CostModel()
    trader = PaperTrader(engine, cost_model)

    signals_generated = signals_approved = signals_rejected = 0
    alert_embeds = []

    for inst in instruments:
        try:
            features = build_feature_matrix(
                engine, inst.symbol, Timeframe.H1, now - timedelta(days=90), now,
                source=cfg.data.provider_live, as_of=now,
            )
        except Exception as exc:  # noqa: BLE001 - un instrumento roto no debe tumbar el resto del universo
            errors.append(f"{inst.symbol}: fallo calculando features ({exc})")
            continue
        if features.empty:
            continue
        latest_ts = features["ts"].iloc[-1]

        for strategy in all_strategies():
            try:
                strategy_signals = strategy.generate_signals(features)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{inst.symbol}/{strategy.name}: fallo generando señales ({exc})")
                continue

            fired_now = strategy_signals[strategy_signals["ts"] == latest_ts]
            for sig in fired_now.itertuples():
                signals_generated += 1
                ts_py = pd.Timestamp(sig.ts).to_pydatetime()
                signal_id = record_signal(
                    engine, ts_py, sig.strategy, sig.instrument, sig.timeframe, sig.direction,
                    sig.entry_price, sig.stop_loss, sig.take_profit, sig.confidence, sig.reason,
                )

                if not can_trade:
                    update_signal_status(engine, signal_id, "rejected", rejections[0])
                    signals_rejected += 1
                    continue

                limit_check = check_new_position_allowed(
                    inst.symbol, portfolio, cfg.account.max_simultaneous_positions,
                    cfg.account.max_correlation_between_positions, correlation_matrix,
                )
                if not limit_check.allowed:
                    update_signal_status(engine, signal_id, "rejected", limit_check.reason)
                    signals_rejected += 1
                    rejections.append(f"{inst.symbol}: {limit_check.reason}")
                    continue

                try:
                    rate = resolve_quote_to_eur_rate(engine, inst.symbol, now, source=cfg.data.provider_live)
                except CurrencyConversionError as exc:
                    update_signal_status(engine, signal_id, "rejected", str(exc))
                    signals_rejected += 1
                    rejections.append(f"{inst.symbol}: {exc}")
                    continue

                trade_id = trader.open_from_signal(
                    signal_id, portfolio.capital_eur, cfg.account.max_risk_per_trade_pct,
                    inst.symbol, sig.strategy, sig.direction, ts_py, sig.entry_price, sig.stop_loss, rate,
                )
                if trade_id is None:
                    signals_rejected += 1
                    rejections.append(f"{inst.symbol}: riesgo disponible no alcanza el lote minimo")
                    continue

                signals_approved += 1
                portfolio.open_positions.append(OpenPositionRef(inst.symbol, sig.direction, 0.0))
                alert_embeds.append(signal_embed(sig._asdict()))

    record_equity(engine, now, "paper", portfolio.capital_eur)
    set_risk_state(engine, "last_daily_job", {
        "ts": now.isoformat(), "signals_generated": signals_generated, "errors": len(errors),
    })

    report = {
        "date": now.date().isoformat(),
        "signals_generated": signals_generated,
        "signals_approved": signals_approved,
        "signals_rejected": signals_rejected,
        "open_positions": len(portfolio.open_positions),
        "capital_eur": portfolio.capital_eur,
        "kill_switch_suspended": kill_switch_status.suspended,
        "rejections": rejections,
        "errors": errors,
    }

    _notify(settings, report, alert_embeds, kill_switch_status)
    return report


def _ingest_calendar_and_news(engine, cfg) -> list[str]:
    errors: list[str] = []

    calendar_provider = ForexFactoryCalendarProvider()
    try:
        calendar_df = calendar_provider.fetch_calendar(
            cfg.news.calendar_lookahead_days, cfg.news.calendar_lookback_days
        )
        accepted = upsert_calendar_events(engine, calendar_df)
        log_ingestion(engine, "calendar", calendar_provider.name, len(calendar_df), accepted, "ok")
    except Exception as exc:  # noqa: BLE001 - degradacion elegante: se avisa, no se inventa calendario
        errors.append(f"calendario: {exc}")
        log_ingestion(engine, "calendar", calendar_provider.name, 0, 0, "failed", issues={"error": str(exc)})
        logger.exception("fallo ingiriendo calendario")

    news_provider = RSSNewsProvider(cfg.news.rss_sources)
    try:
        news_df = news_provider.fetch_headlines()
        accepted = upsert_news_headlines(engine, news_df)
        log_ingestion(engine, "news", news_provider.name, len(news_df), accepted, "ok")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"noticias: {exc}")
        log_ingestion(engine, "news", news_provider.name, 0, 0, "failed", issues={"error": str(exc)})
        logger.exception("fallo ingiriendo noticias")

    return errors


def _evaluate_kill_switch(engine, cfg, now: datetime):
    equity_series = read_equity_curve(engine, "paper", _EPOCH, now)
    return evaluate_from_equity_curve(
        engine, equity_series, cfg.backtest.reference_max_drawdown_pct, cfg.account.drawdown_suspend_pct
    )


def _build_correlation_matrix(engine, instruments, source: str, as_of: datetime) -> pd.DataFrame:
    start = as_of - timedelta(days=120)
    returns: dict[str, pd.Series] = {}
    for inst in instruments:
        df = read_ohlcv(engine, inst.symbol, "D1", start, as_of, source=source, as_of=as_of)
        if len(df) < 10:
            continue
        df = df.sort_values("ts")
        log_returns = np.log(df["close"] / df["close"].shift(1)).dropna()
        returns[inst.symbol] = pd.Series(log_returns.values, index=df["ts"].iloc[1:].values)

    if not returns:
        return pd.DataFrame()
    return compute_correlation_matrix(returns, window=60)


def _notify(settings, report: dict, alert_embeds: list, kill_switch_status) -> None:
    async def _run() -> None:
        await send_embed(settings.discord_reports_channel_id, daily_report_embed(report))
        await send_embeds(settings.discord_alerts_channel_id, alert_embeds)
        if kill_switch_status.suspended and report["signals_generated"] == 0:
            # Se acaba de detectar la suspension en este mismo run (no habia
            # señales que generar porque ya estaba cortado el paso): avisar
            # explicitamente en vez de dejar que se note solo por ausencia.
            await send_embed(settings.discord_alerts_channel_id, kill_switch_alert_embed(kill_switch_status))

    asyncio.run(_run())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    result = run()
    logger.info("daily_job completado: %s", result)
