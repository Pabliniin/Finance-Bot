"""Kill switch de drawdown: si el drawdown EN VIVO supera lo que el backtest
ya advirtio que era plausible (mas un margen), algo se ha roto respecto a lo
esperado - un cambio de regimen de mercado, un bug, un broker que empezo a
dar peor ejecucion. El bot se suspende y no vuelve a emitir señales solas
hasta que una persona lo revise y lo reactive explicitamente. Este modulo
nunca reactiva el switch por si solo, ni siquiera si el drawdown se recupera.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy.engine import Engine

from data.storage.cache import get_risk_state, set_risk_state

_STATE_KEY = "kill_switch"


@dataclass
class KillSwitchStatus:
    suspended: bool
    reason: str | None = None
    since: str | None = None

    def to_dict(self) -> dict:
        return {"suspended": self.suspended, "reason": self.reason, "since": self.since}


def get_status(engine: Engine) -> KillSwitchStatus:
    raw = get_risk_state(engine, _STATE_KEY)
    if raw is None:
        return KillSwitchStatus(suspended=False)
    return KillSwitchStatus(**raw)


def evaluate_drawdown(
    engine: Engine,
    current_drawdown_pct: float,
    backtest_max_drawdown_pct: float,
    suspend_margin_pct: float,
) -> KillSwitchStatus:
    """suspend_margin_pct (config.account.drawdown_suspend_pct): margen
    porcentual SOBRE el drawdown maximo visto en backtest. p.ej. si el
    backtest tuvo un DD maximo de 15% y el margen es 20%, el limite en vivo
    es 15% * 1.20 = 18%."""
    current = get_status(engine)
    if current.suspended:
        return current  # pegajoso: no se reevalua ni se levanta solo

    limit_pct = abs(backtest_max_drawdown_pct) * (1 + suspend_margin_pct / 100)
    if abs(current_drawdown_pct) >= limit_pct:
        status = KillSwitchStatus(
            suspended=True,
            reason=(
                f"drawdown en vivo {abs(current_drawdown_pct):.1f}% alcanza el limite derivado del "
                f"backtest ({limit_pct:.1f}% = {abs(backtest_max_drawdown_pct):.1f}% de DD maximo "
                f"historico + {suspend_margin_pct:.0f}% de margen)"
            ),
            since=datetime.now(UTC).isoformat(),
        )
        set_risk_state(engine, _STATE_KEY, status.to_dict())
        return status

    return current


def evaluate_from_equity_curve(
    engine: Engine,
    equity_curve: pd.Series,
    reference_max_drawdown_pct: float | None,
    suspend_margin_pct: float,
) -> KillSwitchStatus:
    """Envoltorio compartido por scheduler/daily_job.py y
    scheduler/intraday_job.py para no duplicar el calculo de drawdown actual
    en dos sitios. Sin referencia de backtest (aun no se ha corrido
    scripts/run_full_backtest.py) o sin historial de equity, no evalua nada."""
    current = get_status(engine)
    if current.suspended or reference_max_drawdown_pct is None or equity_curve.empty:
        return current

    running_max = equity_curve.cummax()
    current_drawdown_pct = float((equity_curve.iloc[-1] / running_max.iloc[-1] - 1) * 100)
    return evaluate_drawdown(engine, current_drawdown_pct, reference_max_drawdown_pct, suspend_margin_pct)


def manual_reset(engine: Engine, operator_note: str) -> None:
    """Unica forma de levantar el kill switch: una accion humana explicita,
    nunca automatica."""
    status = KillSwitchStatus(suspended=False, reason=f"reactivado manualmente: {operator_note}", since=None)
    set_risk_state(engine, _STATE_KEY, status.to_dict())
