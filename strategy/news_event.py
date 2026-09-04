"""Estrategia de evento/noticia: NO intenta adivinar la direccion de un
evento economico antes de que ocurra (eso no es una ventaja, es una apuesta
sobre informacion desconocida). En su lugar busca CONTINUACION post-evento:
una vela con rango anormalmente amplio (respecto a su propio ATR) justo
despues de un evento de alto impacto, con el sentimiento de las noticias
recientes confirmando esa misma direccion. Es la version defendible de
"tradear la noticia": confirmar con precio + texto lo que ya ocurrio, no
predecir lo que va a ocurrir.

Parametros fijos para todos los instrumentos.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy.base import SIGNAL_COLUMNS, Strategy

_POST_EVENT_WINDOW_HOURS = 3.0
_SHOCK_ATR_MULT = 1.2
_SENTIMENT_THRESHOLD = 0.05
_ATR_TARGET_MULT = 2.5
_ATR_STOP_BUFFER_MULT = 0.3


class NewsEventStrategy(Strategy):
    name = "news_event"

    def generate_signals(self, features: pd.DataFrame) -> pd.DataFrame:
        required = ["open", "close", "high", "low", "atr"]
        df = features.dropna(subset=required).copy()
        if df.empty:
            return self.empty_signals()

        hours_since = df.get("hours_since_last_high_impact_event")
        sentiment = df.get("news_sentiment_mean", pd.Series(0.0, index=df.index)).fillna(0.0)
        mentions = df.get("news_mention_volume", pd.Series(0, index=df.index)).fillna(0)

        if hours_since is None:
            return self.empty_signals()

        recent_event = hours_since.fillna(np.inf) <= _POST_EVENT_WINDOW_HOURS
        bar_range = df["high"] - df["low"]
        shock = bar_range >= df["atr"] * _SHOCK_ATR_MULT
        has_news = mentions > 0

        bullish_shock = shock & (df["close"] > df["open"])
        bearish_shock = shock & (df["close"] < df["open"])

        long_mask = recent_event & has_news & bullish_shock & (sentiment > _SENTIMENT_THRESHOLD)
        short_mask = recent_event & has_news & bearish_shock & (sentiment < -_SENTIMENT_THRESHOLD)

        rows = []
        for idx in df.index[long_mask]:
            row = df.loc[idx]
            entry = float(row.close)
            stop = float(row.low) - float(row.atr) * _ATR_STOP_BUFFER_MULT
            target = entry + float(row.atr) * _ATR_TARGET_MULT
            rows.append(self.build_signal(
                row.ts, row.instrument, row.timeframe, self.name, "long", entry, stop, target,
                confidence=_confidence(sentiment.loc[idx]),
                reason=(
                    f"Evento de alto impacto hace {hours_since.loc[idx]:.1f}h, vela alcista de "
                    f"{bar_range.loc[idx] / row.atr:.2f}x ATR, sentimiento {sentiment.loc[idx]:+.2f} "
                    f"en {int(mentions.loc[idx])} titular(es) confirmando direccion"
                ),
            ))

        for idx in df.index[short_mask]:
            row = df.loc[idx]
            entry = float(row.close)
            stop = float(row.high) + float(row.atr) * _ATR_STOP_BUFFER_MULT
            target = entry - float(row.atr) * _ATR_TARGET_MULT
            rows.append(self.build_signal(
                row.ts, row.instrument, row.timeframe, self.name, "short", entry, stop, target,
                confidence=_confidence(sentiment.loc[idx]),
                reason=(
                    f"Evento de alto impacto hace {hours_since.loc[idx]:.1f}h, vela bajista de "
                    f"{bar_range.loc[idx] / row.atr:.2f}x ATR, sentimiento {sentiment.loc[idx]:+.2f} "
                    f"en {int(mentions.loc[idx])} titular(es) confirmando direccion"
                ),
            ))

        if not rows:
            return self.empty_signals()
        result = pd.DataFrame(rows, columns=SIGNAL_COLUMNS).sort_values("ts").reset_index(drop=True)
        self.validate_signals(result)
        return result


def _confidence(sentiment_value: float) -> float:
    return float(np.clip(0.5 + abs(sentiment_value), 0.3, 1.0))
