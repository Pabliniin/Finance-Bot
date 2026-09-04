"""Features derivadas de noticias y calendario economico: sentimiento,
volumen de menciones y proximidad a eventos. La ingesta cruda vive en
data/providers/news_provider.py y calendar_provider.py; aqui es donde ese
texto/evento se convierte en un numero que una estrategia puede usar.

Sentimiento: VADER (lexicon-based, self-contained, sin descargas externas ni
API de pago) + una pequeña capa de vocabulario financiero propia para paliar
que VADER esta pensado para lenguaje general, no headlines de mercado. Esto
NO es el diccionario academico Loughran-McDonald (su licencia de
redistribucion no es clara para un proyecto que se ejecuta sin conexion
constante) - es deliberadamente mas modesto y hay que tratarlo como una señal
de tono aproximada, no como un sentiment engine de calidad institucional.

Importante: este overlay financiero da tono general (positivo/negativo), NO
interpretacion direccional de politica monetaria (p.ej. "hawkish" no se
etiqueta aqui como bueno o malo para una divisa - esa inferencia depende de
contexto y pertenece a la logica de strategy/news_event.py, no a esta capa).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_analyzer = SentimentIntensityAnalyzer()

# Vocabulario financiero de tono general, complementario a VADER. Pesos
# moderados (no dominan el score de VADER, lo matizan).
_FINANCE_LEXICON = {
    "beats": 2.0, "beat expectations": 2.5, "surges": 2.0, "rally": 1.5,
    "recovery": 1.5, "growth": 1.0, "strong": 1.0, "upbeat": 1.5,
    "misses": -2.0, "miss expectations": -2.5, "plunge": -2.5, "slump": -2.0,
    "recession": -2.5, "crisis": -2.5, "contraction": -1.5, "downturn": -1.5,
    "default": -3.0, "bailout": -1.5, "weak": -1.0,
}

for _term, _weight in _FINANCE_LEXICON.items():
    _analyzer.lexicon[_term] = _weight

CURRENCY_KEYWORDS = {
    "USD": ["usd", "dollar", "fed", "federal reserve", "fomc"],
    "EUR": ["eur", "euro", "ecb", "eurozone"],
    "GBP": ["gbp", "pound", "sterling", "boe", "bank of england"],
    "JPY": ["jpy", "yen", "boj", "bank of japan"],
    "CHF": ["chf", "franc", "snb"],
    "AUD": ["aud", "aussie", "rba"],
    "CAD": ["cad", "loonie", "boc", "bank of canada"],
    "NZD": ["nzd", "kiwi", "rbnz"],
    "XAU": ["gold", "xau", "bullion"],
    "XAG": ["silver", "xag"],
}


def instrument_currencies(symbol: str) -> tuple[str, str]:
    if symbol.startswith(("XAU", "XAG")):
        return symbol[:3], symbol[3:]
    return symbol[:3], symbol[3:]


def score_headline_sentiment(text: str) -> float:
    if not text:
        return 0.0
    return _analyzer.polarity_scores(text)["compound"]


def add_sentiment_scores(headlines_df: pd.DataFrame) -> pd.DataFrame:
    out = headlines_df.copy()
    combined = (out["headline"].fillna("") + ". " + out["summary"].fillna(""))
    out["sentiment_score"] = combined.apply(score_headline_sentiment)
    return out


def _headline_mentions_currency(row: pd.Series, currency: str) -> bool:
    text = f"{row.get('headline', '')} {row.get('summary', '')}".lower()
    return any(kw in text for kw in CURRENCY_KEYWORDS.get(currency, [currency.lower()]))


def aggregate_news_features(
    headlines_df: pd.DataFrame,
    calendar_df: pd.DataFrame,
    instrument: str,
    as_of: pd.Timestamp,
    window_hours: int = 24,
) -> dict:
    """headlines_df y calendar_df deben venir YA filtrados a ts <= as_of por
    el caller para el caso de noticias (ver data.storage.cache.read_news_headlines
    con as_of). calendar_df puede incluir eventos futuros: aqui se separan
    explicitamente pasado/futuro respecto a as_of."""
    base, quote = instrument_currencies(instrument)
    currencies = {base, quote}

    window_start = as_of - pd.Timedelta(hours=window_hours)

    if headlines_df.empty:
        sentiment_mean, mention_volume = 0.0, 0
    else:
        scored = headlines_df if "sentiment_score" in headlines_df.columns else add_sentiment_scores(headlines_df)
        in_window = scored[(scored["ts"] > window_start) & (scored["ts"] <= as_of)]
        relevant = in_window[
            in_window.apply(lambda r: any(_headline_mentions_currency(r, c) for c in currencies), axis=1)
        ]
        mention_volume = len(relevant)
        sentiment_mean = float(relevant["sentiment_score"].mean()) if mention_volume else 0.0

    hours_since_last_event = None
    hours_to_next_event = None
    if not calendar_df.empty:
        relevant_events = calendar_df[calendar_df["currency"].isin(currencies) & (calendar_df["impact"] == "high")]
        past = relevant_events[relevant_events["ts"] <= as_of]
        future = relevant_events[relevant_events["ts"] > as_of]
        if not past.empty:
            hours_since_last_event = (as_of - past["ts"].max()).total_seconds() / 3600
        if not future.empty:
            hours_to_next_event = (future["ts"].min() - as_of).total_seconds() / 3600

    return {
        "news_sentiment_mean": sentiment_mean,
        "news_mention_volume": mention_volume,
        "hours_since_last_high_impact_event": hours_since_last_event,
        "hours_to_next_high_impact_event": hours_to_next_event,
    }


def bulk_news_features(
    bars_ts: pd.Series, headlines_df: pd.DataFrame, instrument: str, window_hours: int = 24
) -> pd.DataFrame:
    """Version vectorizada de la parte de sentimiento de aggregate_news_features,
    para calcular la feature sobre una matriz completa (backtest) en vez de
    punto a punto. Asume bars_ts ya ordenado ascendente (lo garantiza
    data.storage.cache.read_ohlcv). Usa sumas acumuladas + busqueda binaria en
    vez de una ventana rolling sobre eventos irregulares, O(n log m) en total
    en lugar de recorrer el DataFrame de noticias por cada barra."""
    empty = pd.DataFrame(
        {"news_sentiment_mean": 0.0, "news_mention_volume": 0}, index=bars_ts.index
    )
    if headlines_df.empty:
        return empty

    base, quote = instrument_currencies(instrument)
    currencies = {base, quote}
    scored = headlines_df if "sentiment_score" in headlines_df.columns else add_sentiment_scores(headlines_df)
    mask = scored.apply(lambda r: any(_headline_mentions_currency(r, c) for c in currencies), axis=1)
    relevant = scored[mask].sort_values("ts")
    if relevant.empty:
        return empty

    ts_ns = relevant["ts"].astype("int64").to_numpy()
    sentiment = relevant["sentiment_score"].to_numpy()
    cum_sum = np.concatenate([[0.0], np.cumsum(sentiment)])

    bars_ns = pd.to_datetime(bars_ts).astype("int64").to_numpy()
    window_ns = int(pd.Timedelta(hours=window_hours).value)

    lo = np.searchsorted(ts_ns, bars_ns - window_ns, side="right")
    hi = np.searchsorted(ts_ns, bars_ns, side="right")

    counts = hi - lo
    sums = cum_sum[hi] - cum_sum[lo]
    means = np.divide(sums, counts, out=np.zeros_like(sums, dtype=float), where=counts > 0)

    return pd.DataFrame({"news_sentiment_mean": means, "news_mention_volume": counts}, index=bars_ts.index)


def bulk_event_proximity_features(
    bars_ts: pd.Series, calendar_df: pd.DataFrame, instrument: str
) -> pd.DataFrame:
    """Analogo vectorizado a la parte de calendario de aggregate_news_features,
    via merge_asof (busqueda del evento pasado/futuro mas cercano por barra).
    Ver nota point-in-time en features/pipeline.py: la FECHA de un evento
    economico es publica de antemano, así que usar eventos futuros aqui no es
    look-ahead; lo que nunca se usa es el valor 'actual' antes de su hora de
    publicacion."""
    base, quote = instrument_currencies(instrument)
    currencies = {base, quote}
    empty = pd.DataFrame(
        {"hours_since_last_high_impact_event": np.nan, "hours_to_next_high_impact_event": np.nan},
        index=bars_ts.index,
    )

    if calendar_df.empty:
        return empty

    relevant = calendar_df[
        calendar_df["currency"].isin(currencies) & (calendar_df["impact"] == "high")
    ].sort_values("ts")
    if relevant.empty:
        return empty

    left = pd.DataFrame({"ts": pd.to_datetime(bars_ts).to_numpy()})
    right = relevant[["ts"]].reset_index(drop=True)
    right["ts"] = pd.to_datetime(right["ts"])

    past = pd.merge_asof(
        left, right.rename(columns={"ts": "last_event_ts"}),
        left_on="ts", right_on="last_event_ts", direction="backward",
    )
    future = pd.merge_asof(
        left, right.rename(columns={"ts": "next_event_ts"}),
        left_on="ts", right_on="next_event_ts", direction="forward",
    )

    hours_since = (left["ts"] - past["last_event_ts"]).dt.total_seconds() / 3600
    hours_to = (future["next_event_ts"] - left["ts"]).dt.total_seconds() / 3600

    return pd.DataFrame(
        {
            "hours_since_last_high_impact_event": hours_since.to_numpy(),
            "hours_to_next_high_impact_event": hours_to.to_numpy(),
        },
        index=bars_ts.index,
    )
