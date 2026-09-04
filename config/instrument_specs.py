"""Helpers pequeños sobre config/instruments.yaml, reusados tanto por
backtest/ (coste en pips -> precio) como por risk/ (dimensionamiento). Valores
de referencia offline; en produccion, el collector puede sobreescribirlos con
symbol_info() real de MT5 (ver data/providers/mt5_provider.py:symbol_info).
"""

from __future__ import annotations

from functools import lru_cache

from config.loader import Instrument, load_instruments


@lru_cache
def _instrument_map() -> dict[str, Instrument]:
    return {i.symbol: i for i in load_instruments()}


def get_instrument(symbol: str) -> Instrument:
    inst = _instrument_map().get(symbol)
    if inst is None:
        raise KeyError(f"instrumento no configurado en instruments.yaml: {symbol}")
    return inst


def pip_size(symbol: str) -> float:
    return 10 ** -get_instrument(symbol).pip_decimal


def quote_currency(symbol: str) -> str:
    if symbol.startswith(("XAU", "XAG")):
        return symbol[3:]
    return symbol[3:6]


def base_currency(symbol: str) -> str:
    if symbol.startswith(("XAU", "XAG")):
        return symbol[:3]
    return symbol[:3]
