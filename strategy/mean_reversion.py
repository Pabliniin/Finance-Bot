"""Reversion a la media: precio en el extremo de las Bandas de Bollinger +
RSI en zona extrema, filtrado por ADX bajo (mercado lateral). El filtro de
ADX es la parte que mas importa: reversion a la media en un mercado con
tendencia fuerte es la forma clasica de "pillar el cuchillo cayendo".
Parametros fijos para todos los instrumentos.
"""

from __future__ import annotations

import pandas as pd

from strategy.base import SIGNAL_COLUMNS, Strategy

_RSI_OVERSOLD = 30.0
_RSI_OVERBOUGHT = 70.0
_RANGE_ADX_MAX = 20.0
_ATR_STOP_MULT = 1.0
_BASE_CONFIDENCE = 0.5


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def generate_signals(self, features: pd.DataFrame) -> pd.DataFrame:
        required = ["close", "bb_upper", "bb_lower", "bb_mid", "rsi", "adx", "atr"]
        df = features.dropna(subset=required).copy()
        if df.empty:
            return self.empty_signals()

        ranging = df["adx"] <= _RANGE_ADX_MAX
        long_mask = (df["close"] <= df["bb_lower"]) & (df["rsi"] <= _RSI_OVERSOLD) & ranging
        short_mask = (df["close"] >= df["bb_upper"]) & (df["rsi"] >= _RSI_OVERBOUGHT) & ranging

        rows = []
        for idx in df.index[long_mask]:
            row = df.loc[idx]
            entry = float(row.close)
            target = float(row.bb_mid)
            if target <= entry:
                continue  # banda degenerada (mid por debajo del precio de entrada): descartar
            stop = entry - float(row.atr) * _ATR_STOP_MULT
            rows.append(self.build_signal(
                row.ts, row.instrument, row.timeframe, self.name, "long", entry, stop, target,
                confidence=_confidence_from_rsi(row.rsi, _RSI_OVERSOLD, oversold=True),
                reason=(
                    f"Cierre ({entry:.5f}) en/bajo banda inferior de Bollinger ({row.bb_lower:.5f}), "
                    f"RSI={row.rsi:.1f} (sobreventa), ADX={row.adx:.1f} (mercado lateral, <= {_RANGE_ADX_MAX:.0f})"
                ),
            ))

        for idx in df.index[short_mask]:
            row = df.loc[idx]
            entry = float(row.close)
            target = float(row.bb_mid)
            if target >= entry:
                continue
            stop = entry + float(row.atr) * _ATR_STOP_MULT
            rows.append(self.build_signal(
                row.ts, row.instrument, row.timeframe, self.name, "short", entry, stop, target,
                confidence=_confidence_from_rsi(row.rsi, _RSI_OVERBOUGHT, oversold=False),
                reason=(
                    f"Cierre ({entry:.5f}) en/sobre banda superior de Bollinger ({row.bb_upper:.5f}), "
                    f"RSI={row.rsi:.1f} (sobrecompra), ADX={row.adx:.1f} (mercado lateral, <= {_RANGE_ADX_MAX:.0f})"
                ),
            ))

        if not rows:
            return self.empty_signals()
        result = pd.DataFrame(rows, columns=SIGNAL_COLUMNS).sort_values("ts").reset_index(drop=True)
        self.validate_signals(result)
        return result


def _confidence_from_rsi(rsi: float, threshold: float, oversold: bool) -> float:
    extremity = (threshold - rsi) if oversold else (rsi - threshold)
    return max(0.3, min(1.0, _BASE_CONFIDENCE + extremity / 30.0))
