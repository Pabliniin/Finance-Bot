"""Limites duros de cartera: numero maximo de posiciones simultaneas,
correlacion maxima entre posiciones abiertas, y perdida diaria/semanal.

Deliberadamente sin estado propio: la perdida diaria/semanal se calcula al
vuelo a partir de tracking/ledger.py (la tabla trades es la fuente de verdad),
no se duplica un contador aparte que pueda desincronizarse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class OpenPositionRef:
    instrument: str
    direction: str
    risk_eur: float


@dataclass
class PortfolioState:
    open_positions: list[OpenPositionRef] = field(default_factory=list)
    capital_eur: float = 0.0
    daily_pnl_eur: float = 0.0
    weekly_pnl_eur: float = 0.0


@dataclass
class LimitCheckResult:
    allowed: bool
    reason: str


def compute_correlation_matrix(returns_by_instrument: dict[str, pd.Series], window: int = 60) -> pd.DataFrame:
    """returns_by_instrument: retornos diarios (o del timeframe que se use de
    forma consistente) de cada instrumento, ya alineados por fecha. Se usa
    solo la ventana movil mas reciente: la correlacion entre pares de divisas
    no es estable en el tiempo, y usar todo el historico sesgaria hacia
    regimenes de mercado que ya no aplican."""
    aligned = pd.DataFrame(returns_by_instrument).dropna(how="all")
    return aligned.tail(window).corr()


def check_new_position_allowed(
    candidate_instrument: str,
    portfolio: PortfolioState,
    max_simultaneous_positions: int,
    max_correlation: float,
    correlation_matrix: pd.DataFrame | None = None,
) -> LimitCheckResult:
    if any(p.instrument == candidate_instrument for p in portfolio.open_positions):
        return LimitCheckResult(False, f"ya hay una posicion abierta en {candidate_instrument}")

    if len(portfolio.open_positions) >= max_simultaneous_positions:
        return LimitCheckResult(
            False, f"limite de posiciones simultaneas alcanzado ({max_simultaneous_positions})"
        )

    if correlation_matrix is not None and candidate_instrument in correlation_matrix.index:
        for pos in portfolio.open_positions:
            if pos.instrument not in correlation_matrix.columns:
                continue
            corr = correlation_matrix.loc[candidate_instrument, pos.instrument]
            if pd.notna(corr) and abs(corr) > max_correlation:
                return LimitCheckResult(
                    False,
                    f"correlacion {corr:.2f} con la posicion abierta en {pos.instrument} "
                    f"supera el maximo permitido ({max_correlation})",
                )

    return LimitCheckResult(True, "ok")


def check_loss_limits(
    portfolio: PortfolioState, max_daily_loss_pct: float, max_weekly_loss_pct: float
) -> LimitCheckResult:
    if portfolio.capital_eur <= 0:
        return LimitCheckResult(False, "capital de referencia invalido")

    daily_loss_pct = max(0.0, -portfolio.daily_pnl_eur) / portfolio.capital_eur * 100
    weekly_loss_pct = max(0.0, -portfolio.weekly_pnl_eur) / portfolio.capital_eur * 100

    if daily_loss_pct >= max_daily_loss_pct:
        return LimitCheckResult(
            False, f"perdida diaria {daily_loss_pct:.1f}% alcanza o supera el limite ({max_daily_loss_pct}%)"
        )
    if weekly_loss_pct >= max_weekly_loss_pct:
        return LimitCheckResult(
            False, f"perdida semanal {weekly_loss_pct:.1f}% alcanza o supera el limite ({max_weekly_loss_pct}%)"
        )
    return LimitCheckResult(True, "ok")
