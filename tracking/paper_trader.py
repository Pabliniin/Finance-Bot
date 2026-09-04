"""Ejecucion en papel: abre y cierra posiciones simuladas contra precios
reales (via MT5/XM en produccion), aplicando exactamente las mismas reglas de
coste y de "empate stop/target dentro de la misma barra" que
backtest/engine.py. Que ambos motores compartan la misma logica de cierre es
lo que hace que comparar backtest vs. paper trading (tracking/model_monitor.py)
sea una comparacion justa y no manzanas con naranjas.

Este modulo NUNCA ejecuta ordenes reales: solo escribe en la tabla trades con
mode='paper'. La operativa con dinero real es siempre manual del usuario.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.engine import Engine

from backtest.costs import CostModel
from risk.position_sizing import PositionSizingError, compute_position_size
from tracking.ledger import close_trade, open_trade, update_signal_status

_DIRECTION_SIGN = {"long": 1, "short": -1}


class PaperTrader:
    def __init__(self, engine: Engine, cost_model: CostModel, stop_wins_ties: bool = True):
        self.engine = engine
        self.cost_model = cost_model
        self.stop_wins_ties = stop_wins_ties

    def open_from_signal(
        self,
        signal_id: str,
        capital_eur: float,
        risk_pct: float,
        instrument: str,
        strategy: str,
        direction: str,
        ts: datetime,
        entry_price: float,
        stop_loss: float,
        quote_to_eur_rate: float,
    ) -> str | None:
        try:
            size = compute_position_size(
                capital_eur=capital_eur, risk_pct=risk_pct, entry_price=entry_price,
                stop_loss_price=stop_loss, quote_to_eur_rate=quote_to_eur_rate,
            )
        except PositionSizingError as exc:
            update_signal_status(self.engine, signal_id, "rejected", str(exc))
            return None

        cost = self.cost_model.entry_cost_price(instrument)
        sign = _DIRECTION_SIGN[direction]
        fill_price = entry_price + sign * cost

        trade_id = open_trade(
            self.engine, signal_id, "paper", instrument, strategy, direction, ts, fill_price, size.lots, size.risk_eur
        )
        update_signal_status(self.engine, signal_id, "approved")
        return trade_id

    def evaluate_open_position(
        self, position_row, bar_high: float, bar_low: float, bar_ts: datetime, quote_to_eur_rate: float
    ) -> bool:
        """Devuelve True si la posicion se cerro con esta barra."""
        sign = _DIRECTION_SIGN[position_row.direction]

        hit_stop = bar_low <= position_row.stop_loss if sign == 1 else bar_high >= position_row.stop_loss
        hit_target = bar_high >= position_row.take_profit if sign == 1 else bar_low <= position_row.take_profit
        if not (hit_stop or hit_target):
            return False

        if hit_stop and hit_target:
            exit_price, reason = (
                (position_row.stop_loss, "stop") if self.stop_wins_ties else (position_row.take_profit, "target")
            )
        elif hit_stop:
            exit_price, reason = position_row.stop_loss, "stop"
        else:
            exit_price, reason = position_row.take_profit, "target"

        slippage = self.cost_model.slippage_cost_price(position_row.instrument)
        filled_exit = exit_price - sign * slippage

        pnl_quote = (filled_exit - position_row.entry_fill) * position_row.lots * 100_000 * sign
        pnl_eur = pnl_quote * quote_to_eur_rate

        nights = max(0, (bar_ts.date() - position_row.ts_open.date()).days)
        pnl_eur -= self.cost_model.commission_eur(position_row.lots)
        pnl_eur += self.cost_model.swap_eur(position_row.instrument, position_row.direction, nights, position_row.lots)

        close_trade(self.engine, position_row.trade_id, bar_ts, filled_exit, pnl_eur, reason)
        return True
