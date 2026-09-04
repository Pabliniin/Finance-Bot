"""Job horario: revisa las posiciones abiertas en paper trading contra la
ultima vela H1 cerrada y cierra las que tocan stop o take profit, usando la
MISMA logica (tracking/paper_trader.py) que el motor de backtest, para que la
comparacion backtest-vs-vivo (tracking/model_monitor.py) sea justa.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from backtest.costs import CostModel
from bot.formatting import kill_switch_alert_embed
from bot.notifier import send_embed
from config.loader import load_config
from config.settings import get_settings
from data.storage.cache import read_ohlcv
from data.storage.db import get_engine
from risk.currency_conversion import CurrencyConversionError, resolve_quote_to_eur_rate
from risk.kill_switch import evaluate_from_equity_curve, get_status
from tracking.ledger import read_equity_curve, read_open_trades_with_signal, realized_pnl_eur, record_equity
from tracking.paper_trader import PaperTrader

logger = logging.getLogger("scheduler.intraday_job")
_EPOCH = datetime(2020, 1, 1, tzinfo=UTC)


def run() -> dict:
    settings = get_settings()
    cfg = load_config()
    engine = get_engine(settings.database_url)
    now = datetime.now(UTC)

    open_trades = read_open_trades_with_signal(engine, "paper")
    errors: list[str] = []
    closed = 0

    if not open_trades.empty:
        cost_model = CostModel()
        trader = PaperTrader(engine, cost_model)

        for pos in open_trades.itertuples():
            try:
                bars = read_ohlcv(
                    engine, pos.instrument, "H1", now - timedelta(hours=6), now,
                    source=cfg.data.provider_live, as_of=now,
                )
                if bars.empty:
                    continue
                latest = bars.iloc[-1]
                rate = resolve_quote_to_eur_rate(engine, pos.instrument, now, source=cfg.data.provider_live)
            except CurrencyConversionError as exc:
                errors.append(f"{pos.instrument}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - un instrumento roto no debe bloquear el resto
                errors.append(f"{pos.instrument}: {exc}")
                continue

            was_closed = trader.evaluate_open_position(
                pos, float(latest["high"]), float(latest["low"]), latest["ts"].to_pydatetime(), rate
            )
            if was_closed:
                closed += 1

    capital = cfg.account.capital_reference_eur + realized_pnl_eur(engine, "paper", _EPOCH, now)
    record_equity(engine, now, "paper", capital)

    was_suspended_before = get_status(engine).suspended
    equity_series = read_equity_curve(engine, "paper", _EPOCH, now)
    status = evaluate_from_equity_curve(
        engine, equity_series, cfg.backtest.reference_max_drawdown_pct, cfg.account.drawdown_suspend_pct
    )
    if status.suspended and not was_suspended_before:
        asyncio.run(send_embed(settings.discord_alerts_channel_id, kill_switch_alert_embed(status)))

    return {
        "checked": len(open_trades), "closed": closed, "errors": errors,
        "kill_switch_suspended": status.suspended, "capital_eur": capital,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger.info("intraday_job completado: %s", run())
