"""Smoke test de los 20 votantes y los 8 triggers sobre features reales.

No comprueba QUE opina cada estrategia (eso lo decide el mercado), sino que
todas producen una señal valida (-1/0/+1, sin NaN) y que sus textos de
explicacion se renderizan sin reventar, incluso con columnas de contexto
(htf_, d1_, im_) presentes. Asi un votante que referencie una columna que no
existe, o un `describe` que falle con un NaN, se caza aqui y no en vivo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from finance_bot.data.bars import TF_MINUTES
from finance_bot.engine.features import build_features
from finance_bot.strategies.triggers import TRIGGER_KEYS
from finance_bot.strategies.voters import VOTER_KEYS, VOTERS
from tests.conftest import random_walk_bars

_TFS = {"H1": (1600, "1h"), "H4": (500, "4h"), "D1": (400, "1D")}


def _feature_frame():
    """Features H1 completas, con contexto de H4 (htf_), D1 (d1_) e intermercado
    (im_, del 'otro' instrumento), como en vivo."""

    def bundle(seed: int) -> dict:
        out = {}
        for i, (tf, (n, freq)) in enumerate(_TFS.items()):
            bars = random_walk_bars(n, freq=freq, seed=seed + i)
            # close_time = cierre de la vela; en vivo lo añade el resample de data/bars.py
            bars["close_time"] = bars.index + pd.Timedelta(minutes=TF_MINUTES[tf])
            out[tf] = bars
        return out

    return build_features(bundle(1), bundle(7))["H1"]


def test_all_voters_and_triggers_emit_valid_signals() -> None:
    f = _feature_frame()
    cols = [f"vote_{k}" for k in VOTER_KEYS] + [f"trig_{k}" for k in TRIGGER_KEYS]
    for col in cols:
        assert col in f.columns, f"falta la columna {col}"
    values = f[cols]
    assert not values.isna().any().any(), "una estrategia produjo NaN"
    assert set(np.unique(values.to_numpy())) <= {-1.0, 0.0, 1.0}, "una estrategia voto fuera de {-1,0,1}"


def test_every_voter_describe_renders_without_crashing() -> None:
    f = _feature_frame()
    # varias filas (no solo la ultima) por si alguna cae en una rama con NaN
    for _, row in f.iloc[[-1, -50, -200]].iterrows():
        for v in VOTERS:
            text = v.describe(row)
            assert isinstance(text, str) and text, f"{v.key}.describe no devolvio texto"
