"""Nombres legibles (español) de cada feature del modelo, compartidos por el
informe de investigacion y los mensajes de Discord."""

from __future__ import annotations

from finance_bot.strategies.triggers import TRIGGER_NAMES
from finance_bot.strategies.voters import VOTERS

_VOTER_NAMES = {f"agree_{v.key}": v.name for v in VOTERS}
_TRIGGER_NAMES = {f"trig_{k}": f"Entrada: {n}" for k, n in TRIGGER_NAMES.items()}
_OTHER = {
    "rsi_d": "RSI a favor",
    "sl_atr": "Stop ancho (en ATR)",
    "slope200_d": "Pendiente de la EMA200 a favor",
    "room_r": "Espacio hasta el siguiente nivel",
    "atr_rank": "Volatilidad relativa",
    "adx": "Fuerza de tendencia (ADX)",
    "htf_rsi_d": "RSI de la temporalidad superior a favor",
    "dist_ema200_d": "Distancia a la EMA200 a favor",
    "roc_d": "Impulso reciente a favor",
    "bb_pos_d": "Posicion en Bollinger a favor",
    "n_triggers": "Varias entradas a la vez",
    "is_long": "Operacion en largo",
}
_SESSIONS = {
    "asia": "Asia",
    "london": "Londres",
    "overlap": "Londres-Nueva York",
    "newyork": "Nueva York",
    "late": "cierre NY",
}


def feature_label(name: str) -> str:
    if name.startswith("tf_"):
        return f"Temporalidad {name[3:]}"
    if name.startswith("sym_"):
        return f"Instrumento {name[4:]}"
    if name.startswith("session_"):
        return f"Sesion de {_SESSIONS.get(name[8:], name[8:])}"
    return _VOTER_NAMES.get(name) or _TRIGGER_NAMES.get(name) or _OTHER.get(name) or name
