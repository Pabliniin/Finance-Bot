"""Registro central de estrategias. backtest/, scheduler/ y bot/ iteran este
registro en vez de importar cada estrategia por su nombre, para que añadir
una nueva estrategia sea un cambio de una linea aqui, no en cada consumidor.
"""

from __future__ import annotations

from strategy.base import Strategy
from strategy.mean_reversion import MeanReversionStrategy
from strategy.news_event import NewsEventStrategy
from strategy.trend_following import TrendFollowingStrategy

STRATEGY_CLASSES: dict[str, type[Strategy]] = {
    "trend_following": TrendFollowingStrategy,
    "mean_reversion": MeanReversionStrategy,
    "news_event": NewsEventStrategy,
}


def build_strategy(name: str) -> Strategy:
    cls = STRATEGY_CLASSES.get(name)
    if cls is None:
        raise KeyError(f"estrategia no registrada: {name}. Disponibles: {list(STRATEGY_CLASSES)}")
    return cls()


def all_strategies() -> list[Strategy]:
    return [cls() for cls in STRATEGY_CLASSES.values()]
