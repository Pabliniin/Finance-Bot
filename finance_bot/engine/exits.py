"""Avisos de gestion de una señal abierta: cuando el contexto que la justifico
deja de estar, cuando se acerca el cierre por tiempo o cuando hay una noticia
fuerte encima.

HONESTIDAD: lo validado es el plan completo (mantener hasta stop, objetivo o
cierre por tiempo). Cerrar antes cambia la estadistica y NO esta respaldado por
el backtest. Por eso cada aviso:
- dice en que R vas en ese momento,
- se guarda en la base de datos con ese R,
- y al cerrar la operacion el bot compara "cerrar en el aviso" con "seguir el
  plan", para que con el tiempo sepas si los avisos ayudan o estorban.

Las reglas estan declaradas de antemano aqui, no ajustadas a ningun resultado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from finance_bot.config import AppConfig
from finance_bot.data.calendar import EconomicEvent
from finance_bot.engine.signals import TimeframeView

# Cuando el contexto de la temporalidad se gira con esta fuerza (votos netos en
# contra sobre 20), lo que justificaba la entrada ya no esta.
REVERSAL_SCORE = 6
# Aviso de cierre por tiempo cuando queda esto o menos de la vida del plan.
TIME_WARNING_FRACTION = 0.2
# Beneficio devuelto: se avisa si llego a >= 1R y ha devuelto >= 0.6R desde el maximo.
GIVEBACK_MIN_PEAK_R = 1.0
GIVEBACK_DROP_R = 0.6

URGENCY_ORDER = {"alta": 0, "media": 1}


@dataclass
class ExitAdvice:
    key: str
    symbol: str
    tf: str
    direction: int
    kind: str
    urgency: str
    headline: str
    detail: str
    r_now: float | None

    @property
    def side(self) -> str:
        return "COMPRA" if self.direction > 0 else "VENTA"


def r_now(entry: float, r_price: float, direction: int, quote: tuple[float, float] | None) -> float | None:
    """R latente cerrando AHORA: una compra se cierra en el bid y una venta en
    el ask (el lado malo del spread, como en el backtest)."""
    if quote is None or r_price <= 0:
        return None
    price = quote[0] if direction > 0 else quote[1]
    return (price - entry) * direction / r_price


def evaluate(
    signal: pd.Series,
    view: TimeframeView | None,
    news: list[EconomicEvent],
    quote: tuple[float, float] | None,
    cfg: AppConfig,
    now: datetime,
    peak_r: float | None = None,
) -> list[ExitAdvice]:
    """Avisos para UNA señal abierta, ordenados por urgencia. Sin view (sin
    datos frescos de esa temporalidad) solo se evaluan tiempo y noticias."""
    direction = int(signal["direction"])
    tf = str(signal["tf"])
    current = r_now(float(signal["entry"]), float(signal["r_price"]), direction, quote)
    out: list[ExitAdvice] = []

    def add(kind: str, urgency: str, headline: str, detail: str) -> None:
        out.append(
            ExitAdvice(
                key=str(signal["key"]),
                symbol=str(signal["symbol"]),
                tf=tf,
                direction=direction,
                kind=kind,
                urgency=urgency,
                headline=headline,
                detail=detail,
                r_now=current,
            )
        )

    signal_time = pd.Timestamp(signal["signal_time"])
    signal_time = signal_time.tz_localize("UTC") if signal_time.tzinfo is None else signal_time.tz_convert("UTC")
    # El contexto solo cuenta con velas posteriores a la de la señal: la misma
    # vela que la genero no puede a la vez "girarse en contra".
    if view is not None and pd.Timestamp(view.bar_close) > signal_time:
        against = -view.score * direction  # votos netos EN CONTRA de la operacion
        if against >= REVERSAL_SCORE:
            add(
                "reversion",
                "alta",
                "El contexto se ha girado en contra",
                f"En {tf} hay {against}/20 votos netos en contra de tu {('compra' if direction > 0 else 'venta')}. "
                "Lo que justificaba la entrada ya no esta.",
            )
        opposite = view.triggers_short if direction > 0 else view.triggers_long
        if opposite:
            add(
                "trigger_opuesto",
                "alta",
                "Ha disparado una entrada en sentido contrario",
                f"En {tf}: {', '.join(opposite)}. El mercado esta dando la señal opuesta a la tuya.",
            )

    blackout = cfg.signals.news_blackout_hours.get(tf, 0)
    if blackout:
        soon = [e for e in news if now <= e.time <= now + timedelta(hours=blackout)]
        if soon:
            event = soon[0]
            add(
                "noticia",
                "media",
                "Noticia fuerte a la vista",
                f"{event.currency} {event.title} a las {event.time:%H:%M} UTC. "
                "En estas noticias el precio salta y el stop puede ejecutarse peor de lo previsto.",
            )

    max_hours = float(signal["max_hours"])
    hours_open = (pd.Timestamp(now).tz_convert("UTC") - signal_time).total_seconds() / 3600
    remaining = max_hours - hours_open
    if 0 < remaining <= max_hours * TIME_WARNING_FRACTION:
        add(
            "tiempo",
            "media",
            "Se acaba el tiempo del plan",
            f"El plan cierra por tiempo en {remaining:.0f} h. Las operaciones que no han llegado al objetivo a "
            "estas alturas rara vez lo hacen.",
        )

    if peak_r is not None and current is not None and peak_r >= GIVEBACK_MIN_PEAK_R:
        given = peak_r - current
        if given >= GIVEBACK_DROP_R:
            add(
                "beneficio",
                "alta",
                "Estas devolviendo beneficio",
                f"Llego a {peak_r:+.2f}R y ahora va {current:+.2f}R ({given:.2f}R devueltos).",
            )

    out.sort(key=lambda a: URGENCY_ORDER.get(a.urgency, 9))
    return out
