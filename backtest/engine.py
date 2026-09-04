"""Motor de backtest evento a evento (bar-by-bar), NO vectorizado: el estado
(posicion abierta, capital) se actualiza secuencialmente barra a barra, igual
que ocurriria en produccion. Esto es deliberado y no negociable en este
proyecto - un backtest vectorizado (aplicar la señal a todo el vector de
precios de una vez) esconde con facilidad look-ahead y no refleja que el
capital disponible para la operacion N depende del resultado de la operacion
N-1 (position sizing compuesto).

Alcance de ESTE motor: una sola combinacion instrumento+timeframe a la vez,
una posicion abierta como maximo. Los limites de cartera (correlacion, numero
maximo de posiciones simultaneas entre instrumentos) son responsabilidad de
risk/limits.py, que orquesta multiples instancias de este motor compartiendo
un unico capital - ver ese modulo.

Simplificaciones documentadas (MVP, no escondidas):
- La curva de equity solo se actualiza en el CIERRE de cada operacion (P&L
  realizado). No hay marcado a mercado intra-operacion, asi que el drawdown
  "flotante" dentro de una operacion abierta no aparece hasta que cierra.
- El tipo de cambio a EUR usado para convertir el P&L es el vigente en la
  APERTURA de la operacion (el mismo que se uso para dimensionar el riesgo),
  no se re-consulta al cierre. Para operaciones de 2-10 dias esto introduce un
  error de segundo orden (variacion del cruce EUR/divisa_cotizacion durante el
  hold), aceptable para esta fase pero a revisar si se busca precision alta.
- No se modela margen/apalancamiento ni llamadas de margen: con el
  dimensionamiento por riesgo fijo de este proyecto (max 5% por operacion,
  max 3 posiciones) el uso de margen en una cuenta XM Standard queda muy por
  debajo de cualquier limite realista, pero si algun dia se sube el riesgo
  por operacion o el numero de posiciones, esto habria que revisarlo.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest.costs import CostModel
from risk.position_sizing import PositionSizingError, compute_position_size

_DIRECTION_SIGN = {"long": 1, "short": -1}


@dataclass
class ClosedTrade:
    instrument: str
    strategy: str
    direction: str
    ts_open: pd.Timestamp
    ts_close: pd.Timestamp
    entry_price: float
    exit_price: float
    lots: float
    risk_eur: float
    pnl_eur: float
    pnl_pct_of_capital: float
    exit_reason: str  # 'stop' | 'target' | 'backtest_end'
    signal_reason: str


@dataclass
class _OpenPosition:
    instrument: str
    strategy: str
    direction: str
    ts_open: pd.Timestamp
    entry_price: float
    stop_loss: float
    take_profit: float
    lots: float
    risk_eur: float
    capital_at_open: float
    quote_to_eur_rate: float
    signal_reason: str


class BacktestEngine:
    def __init__(
        self,
        cost_model: CostModel,
        initial_capital_eur: float,
        risk_pct: float,
        stop_wins_ties: bool = True,
    ):
        self.cost_model = cost_model
        self.risk_pct = risk_pct
        self.stop_wins_ties = stop_wins_ties
        self.capital = initial_capital_eur
        self._position: _OpenPosition | None = None
        self.trades: list[ClosedTrade] = []
        self.equity_curve: list[tuple[pd.Timestamp, float]] = [(pd.Timestamp.min, initial_capital_eur)]

    def run(
        self,
        instrument: str,
        bars: pd.DataFrame,
        signals: pd.DataFrame,
        quote_to_eur_rate_for_ts,
    ) -> tuple[list[ClosedTrade], pd.Series]:
        """quote_to_eur_rate_for_ts: callable(ts) -> float. Vive fuera del
        motor para que quien orqueste el backtest decida como resolverlo
        (p.ej. leyendo la vela de EURUSD/EURJPY correspondiente)."""
        signals_by_ts = {row.ts: row for row in signals.itertuples(index=False)} if not signals.empty else {}

        for bar in bars.itertuples(index=False):
            if self._position is not None:
                self._maybe_close_on_bar(bar)

            if self._position is None and bar.ts in signals_by_ts:
                self._maybe_open(instrument, bar, signals_by_ts[bar.ts], quote_to_eur_rate_for_ts)

        if self._position is not None:
            last_bar = bars.iloc[-1]
            self._force_close(last_bar, reason="backtest_end")

        equity_series = pd.Series(
            data=[e for _, e in self.equity_curve[1:]],
            index=[t for t, _ in self.equity_curve[1:]],
            name="equity_eur",
        )
        return self.trades, equity_series

    def _maybe_open(self, instrument: str, bar, signal, quote_to_eur_rate_for_ts) -> None:
        rate = quote_to_eur_rate_for_ts(bar.ts)
        try:
            size = compute_position_size(
                capital_eur=self.capital,
                risk_pct=self.risk_pct,
                entry_price=signal.entry_price,
                stop_loss_price=signal.stop_loss,
                quote_to_eur_rate=rate,
            )
        except PositionSizingError:
            return  # riesgo no alcanza el lote minimo: no se opera, no es un error del motor

        cost = self.cost_model.entry_cost_price(instrument)
        sign = _DIRECTION_SIGN[signal.direction]
        fill_price = signal.entry_price + sign * cost  # el coste siempre juega en contra

        self._position = _OpenPosition(
            instrument=instrument,
            strategy=signal.strategy,
            direction=signal.direction,
            ts_open=bar.ts,
            entry_price=fill_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            lots=size.lots,
            risk_eur=size.risk_eur,
            capital_at_open=self.capital,
            quote_to_eur_rate=rate,
            signal_reason=signal.reason,
        )

    def _maybe_close_on_bar(self, bar) -> None:
        pos = self._position
        if pos is None:
            return  # defensivo: el caller ya comprueba esto, pero el metodo no debe asumirlo ciegamente
        sign = _DIRECTION_SIGN[pos.direction]

        hit_stop = bar.low <= pos.stop_loss if sign == 1 else bar.high >= pos.stop_loss
        hit_target = bar.high >= pos.take_profit if sign == 1 else bar.low <= pos.take_profit

        if hit_stop and hit_target:
            # Ambiguo dentro de la misma barra: se asume el peor caso (stop
            # primero) salvo que se configure lo contrario explicitamente.
            exit_price, reason = (pos.stop_loss, "stop") if self.stop_wins_ties else (pos.take_profit, "target")
        elif hit_stop:
            exit_price, reason = pos.stop_loss, "stop"
        elif hit_target:
            exit_price, reason = pos.take_profit, "target"
        else:
            return

        self._close(bar.ts, exit_price, reason)

    def _force_close(self, last_bar, reason: str) -> None:
        self._close(last_bar.ts, last_bar.close, reason)

    def _close(self, ts_close: pd.Timestamp, raw_exit_price: float, reason: str) -> None:
        pos = self._position
        if pos is None:
            raise RuntimeError("_close() llamado sin una posicion abierta: fallo interno del motor")
        sign = _DIRECTION_SIGN[pos.direction]
        slippage = self.cost_model.slippage_cost_price(pos.instrument)
        exit_price = raw_exit_price - sign * slippage  # el slippage tambien juega en contra al salir

        pnl_quote = (exit_price - pos.entry_price) * pos.lots * 100_000 * sign
        pnl_eur = pnl_quote * pos.quote_to_eur_rate

        nights = max(0, (ts_close.date() - pos.ts_open.date()).days)
        pnl_eur -= self.cost_model.commission_eur(pos.lots)
        pnl_eur += self.cost_model.swap_eur(pos.instrument, pos.direction, nights, pos.lots)

        self.capital += pnl_eur
        pnl_pct = pnl_eur / pos.capital_at_open if pos.capital_at_open else 0.0

        self.trades.append(
            ClosedTrade(
                instrument=pos.instrument,
                strategy=pos.strategy,
                direction=pos.direction,
                ts_open=pos.ts_open,
                ts_close=ts_close,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                lots=pos.lots,
                risk_eur=pos.risk_eur,
                pnl_eur=pnl_eur,
                pnl_pct_of_capital=pnl_pct,
                exit_reason=reason,
                signal_reason=pos.signal_reason,
            )
        )
        self.equity_curve.append((ts_close, self.capital))
        self._position = None
