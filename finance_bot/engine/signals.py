"""Motor de señales en vivo.

Para cada instrumento y temporalidad, sobre la ULTIMA vela cerrada:
1. Opinion de los 20 votantes y de las 8 estrategias de entrada.
2. Si alguna entrada dispara -> candidato con el plan del grupo (el validado,
   o en modo informativo el mejor disponible, marcado como NO validado).
3. Probabilidad calibrada de TP1/TP2 (modelo walk-forward), escalera de
   objetivos y duracion a partir de CASOS SIMILARES fuera de muestra.
4. Costes, noticias, tamaño de posicion y todas las razones por las que una
   señal se emite o no. Nada se oculta: una señal bloqueada sigue visible en
   /analisis con sus motivos.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from finance_bot.config import PROJECT_ROOT, AppConfig
from finance_bot.data.bars import TF_MINUTES
from finance_bot.data.calendar import EconomicCalendar, EconomicEvent
from finance_bot.data.market import MarketData
from finance_bot.engine.candidates import generate_candidates, model_features
from finance_bot.engine.features import build_features
from finance_bot.engine.model import ProbabilityModel
from finance_bot.research.evaluation import wilson_interval
from finance_bot.risk import PositionSize, position_size
from finance_bot.strategies.labels import feature_label
from finance_bot.strategies.triggers import TRIGGER_NAMES
from finance_bot.strategies.voters import VOTERS

logger = logging.getLogger(__name__)

MODELS_DIR = PROJECT_ROOT / "models"
LADDER_R = (0.5, 1.0, 1.5, 2.0, 3.0)
# El backtest entra justo al cierre de la vela. Una señal que llega mas tarde
# (datos con retraso, o un bloqueo que se levanta horas despues) ya no es la
# misma operacion que se valido: no se emite.
MAX_EMIT_DELAY = {
    "M15": timedelta(minutes=10),
    "H1": timedelta(minutes=20),
    "H4": timedelta(minutes=60),
    "D1": timedelta(hours=3),
}
H1_HISTORY_DAYS = 1100  # D1: ~700 sesiones -> EMA200 y percentiles de ATR estables
M1_HISTORY_DAYS = 40  # M15: ~2.500 velas
# Con MT5 y mercado abierto, mas de esto sin velas nuevas es un terminal sin conexion.
STALE_REALTIME = timedelta(minutes=15)


@dataclass
class VoteView:
    key: str
    name: str
    family: str
    vote: int
    detail: str


@dataclass
class TimeframeView:
    tf: str
    bar_close: pd.Timestamp
    close: float
    atr: float
    rsi: float
    adx: float
    support: float | None
    resistance: float | None
    vol_rank: float | None
    votes: list[VoteView]
    triggers_long: list[str]
    triggers_short: list[str]

    @property
    def score(self) -> int:
        return sum(v.vote for v in self.votes)

    @property
    def bias(self) -> str:
        if self.score >= 4:
            return "alcista"
        if self.score <= -4:
            return "bajista"
        return "neutral"


@dataclass
class LadderStep:
    r: float
    probability: float
    ci_low: float
    ci_high: float


@dataclass
class Signal:
    key: str
    symbol: str
    tf: str
    direction: int
    plan: str
    validated: bool
    signal_time: pd.Timestamp
    entry: float
    stop: float
    tp1: float
    tp2: float
    r_price: float
    targets_r: tuple[float, float]
    partial: float
    p_tp1: float
    p_tp2: float
    threshold: float
    ladder: list[LadderStep]
    similar_n: int
    similar_scope: str
    similar_ev: float | None
    similar_tp1_rate: float | None
    hours_median: float | None
    hours_p75: float | None
    hours_to_tp1_median: float | None
    max_hours: float
    triggers: list[str]
    votes_for: list[VoteView]
    votes_against: list[VoteView]
    top_factors: list[tuple[str, float]]
    news: list[EconomicEvent]
    live_quote: bool
    cost_r: float
    size: PositionSize | None
    # Zona de entrada: entre el precio de referencia (lo validado) y un precio
    # algo mejor. Informativa; la estadistica es la de entrar en la referencia.
    entry_low: float | None = None
    entry_high: float | None = None
    extra_target_r: float | None = None
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def targets(self) -> list[tuple[str, float, float, float | None]]:
        """(etiqueta, precio, R, probabilidad historica de llegar antes del stop).
        Los dos primeros son los del plan validado; el tercero es extra."""
        ladder = {step.r: step.probability for step in self.ladder}

        def row(label: str, r: float) -> tuple[str, float, float, float | None]:
            return label, self.entry + self.direction * self.r_price * r, r, ladder.get(r)

        out = [row("TP1", self.targets_r[0]), row("TP2", self.targets_r[1])]
        if self.extra_target_r and self.extra_target_r > self.targets_r[1]:
            out.append(row("TP3", self.extra_target_r))
        return out

    @property
    def emit(self) -> bool:
        return not self.blockers

    @property
    def side(self) -> str:
        return "COMPRA" if self.direction > 0 else "VENTA"


@dataclass
class Analysis:
    symbol: str
    generated_at: datetime
    data_until: pd.Timestamp | None
    feed: str
    quote: tuple[float, float] | None
    views: dict[str, TimeframeView]
    signals: list[Signal]
    news: list[EconomicEvent]
    warnings: list[str]


@dataclass
class Artifacts:
    validation: dict
    models: dict[str, ProbabilityModel]
    histories: dict[str, pd.DataFrame]

    @classmethod
    def load(cls, models_dir: Path = MODELS_DIR) -> Artifacts | None:
        path = models_dir / "validation.json"
        if not path.exists():
            return None
        validation = json.loads(path.read_text(encoding="utf-8"))
        models, histories = {}, {}
        for plan in validation.get("plans", {}):
            model_path = models_dir / plan / "model.json"
            history_path = models_dir / plan / "oos_history.parquet"
            if model_path.exists() and history_path.exists():
                models[plan] = ProbabilityModel.from_json(model_path)
                histories[plan] = pd.read_parquet(history_path)
        return cls(validation, models, histories) if models else None

    def plan_for(self, group: str) -> tuple[str, bool]:
        """(plan, validado). Sin plan validado: el de mejor t-estadistico en
        validacion para ese grupo (solo se usa en modo informativo)."""
        selected = self.validation.get("selection", {}).get(group)
        if selected in self.models:
            return selected, True
        best, best_t = next(iter(self.models)), -np.inf
        for plan, info in self.validation.get("plans", {}).items():
            t = (info.get("groups", {}).get(group, {}).get("summary", {}) or {}).get("t_stat")
            if plan in self.models and t is not None and t > best_t:
                best, best_t = plan, t
        return best, False

    def threshold(self, plan: str) -> float:
        return float(self.validation["plans"][plan]["threshold_tp1"])


def similar_cases(
    history: pd.DataFrame, symbol: str, tf: str, direction: int, p: float, min_n: int
) -> tuple[pd.DataFrame, str]:
    near = (history["p_tp1"] - p).abs() <= 0.05
    same = (history["symbol"] == symbol) & (history["tf"] == tf)
    tiers = [
        (same & (history["direction"] == direction) & near, f"{symbol} {tf}, misma direccion y probabilidad (±5 pp)"),
        (same & near, f"{symbol} {tf}, probabilidad similar (±5 pp)"),
        ((history["tf"] == tf) & near, f"{tf} en ambos instrumentos, probabilidad similar"),
        (same, f"todas las señales {symbol} {tf}"),
    ]
    for mask, label in tiers:
        subset = history[mask]
        if len(subset) >= min_n:
            return subset, label
    return history[tiers[-1][0]], tiers[-1][1]


def probability_ladder(cases: pd.DataFrame) -> list[LadderStep]:
    steps = []
    n = len(cases)
    for r in LADDER_R:
        hits = float((cases["mfe_r"] >= r).sum()) if n else 0.0
        lo, hi = wilson_interval(hits, n)
        steps.append(LadderStep(r, hits / n if n else float("nan"), lo, hi))
    return steps


class SignalEngine:
    def __init__(
        self, cfg: AppConfig, md: MarketData, artifacts: Artifacts | None, calendar: EconomicCalendar | None = None
    ):
        self.cfg = cfg
        self.md = md
        self.artifacts = artifacts
        self.calendar = calendar

    def analyze(
        self,
        symbol: str,
        *,
        complete_until: pd.Timestamp | None,
        quote: tuple[float, float] | None,
        feed_name: str,
        realtime: bool,
        capital_eur: float,
        risk_pct: float,
        mode: str,
        now: datetime | None = None,
    ) -> Analysis:
        now = now or datetime.now(UTC)
        symbols = list(self.cfg.instruments)
        other = next((s for s in symbols if s != symbol), None)
        timeframes = [tf for tf in self.cfg.timeframes if realtime or tf != "M15"]
        warnings: list[str] = []
        if not realtime:
            warnings.append("Fuente sin tiempo real (Dukascopy): M15 desactivado y datos con hasta ~1h de retraso.")
        elif complete_until is not None and now - complete_until.to_pydatetime() > STALE_REALTIME:
            minutes = int((now - complete_until.to_pydatetime()).total_seconds() // 60)
            warnings.append(
                f"MT5 lleva {minutes} min sin velas nuevas: ¿terminal sin conexion con el broker o mercado cerrado? "
                "Con datos viejos no se emiten señales."
            )

        start = now - timedelta(days=H1_HISTORY_DAYS)
        m1_start = now - timedelta(days=M1_HISTORY_DAYS)
        bars = self.md.all_timeframes(symbol, timeframes, start=start, complete_until=complete_until, m1_start=m1_start)
        other_bars = (
            self.md.all_timeframes(other, timeframes, start=start, complete_until=complete_until, m1_start=m1_start)
            if other
            else None
        )
        feats = build_features(bars, other_bars)
        eurusd = self._eurusd_rate(bars if symbol == "EURUSD" else other_bars)

        news: list[EconomicEvent] = []
        if self.calendar is not None:
            news = self.calendar.upcoming(self.cfg.signals.news_currencies, hours=72, now=now)
            if self.calendar.last_error:
                warnings.append("Calendario economico no disponible: no se puede descartar noticias proximas.")

        views: dict[str, TimeframeView] = {}
        signals: list[Signal] = []
        data_until = None
        for tf in timeframes:
            f = feats.get(tf)
            if f is None or f.empty:
                warnings.append(f"{tf}: sin datos suficientes.")
                continue
            last = f.iloc[-1]
            data_until = max(data_until, last["close_time"]) if data_until is not None else last["close_time"]
            views[tf] = self._view(tf, last)

            stale = complete_until is not None and last["close_time"] < complete_until - timedelta(
                minutes=TF_MINUTES[tf]
            )
            plan_tf = self.cfg.timeframes[tf]
            allowed = self.cfg.sessions_utc.allowed_hours if tf in self.cfg.sessions_utc.intraday_timeframes else None
            cand = generate_candidates(f, symbol, tf, plan_tf, allowed)
            if cand.empty or cand.index[-1] != f.index[-1] or stale:
                continue
            signal = self._build_signal(
                symbol, tf, cand.iloc[[-1]], views[tf], quote, eurusd, capital_eur, risk_pct, mode, news, now
            )
            if signal is not None:
                signals.append(signal)

        if self.artifacts is None:
            warnings.append(
                "Modelo no entrenado todavia (ejecuta `python -m finance_bot research`): "
                "solo analisis, sin probabilidades."
            )
        return Analysis(symbol, now, data_until, feed_name, quote, views, signals, news, warnings)

    # ------------------------------------------------------------------

    @staticmethod
    def _eurusd_rate(bars: dict[str, pd.DataFrame] | None) -> float | None:
        if not bars:
            return None
        for tf in ("M15", "H1", "H4", "D1"):
            b = bars.get(tf)
            if b is not None and not b.empty:
                return float(b["close"].iloc[-1])
        return None

    @staticmethod
    def _view(tf: str, row: pd.Series) -> TimeframeView:
        votes = [VoteView(v.key, v.name, v.family, int(row[f"vote_{v.key}"]), v.describe(row)) for v in VOTERS]
        trig_long = [TRIGGER_NAMES[k[5:]] for k in row.index if k.startswith("trig_") and row[k] == 1]
        trig_short = [TRIGGER_NAMES[k[5:]] for k in row.index if k.startswith("trig_") and row[k] == -1]

        def opt(value) -> float | None:
            return None if pd.isna(value) else float(value)

        return TimeframeView(
            tf=tf,
            bar_close=row["close_time"],
            close=float(row["close"]),
            atr=float(row["atr"]),
            rsi=float(row["rsi"]),
            adx=float(row["adx"]) if pd.notna(row["adx"]) else float("nan"),
            support=opt(row.get("support")),
            resistance=opt(row.get("resistance")),
            vol_rank=opt(row.get("atr_pct_rank")),
            votes=votes,
            triggers_long=trig_long,
            triggers_short=trig_short,
        )

    def _build_signal(
        self,
        symbol: str,
        tf: str,
        cand: pd.DataFrame,
        view: TimeframeView,
        quote: tuple[float, float] | None,
        eurusd: float | None,
        capital_eur: float,
        risk_pct: float,
        mode: str,
        news: list[EconomicEvent],
        now: datetime,
    ) -> Signal | None:
        if self.artifacts is None:
            return None
        row = cand.iloc[0]
        group = f"{symbol} {tf}"
        plan_name, validated = self.artifacts.plan_for(group)
        plan = self.cfg.plans[plan_name]
        model = self.artifacts.models[plan_name]
        direction = int(row["direction"])

        X = model_features(cand)
        probs = model.predict(X)
        p1, p2 = float(probs["p_tp1"].iloc[0]), float(probs["p_tp2"].iloc[0])
        tau = self.artifacts.threshold(plan_name)

        inst = self.cfg.instrument(symbol)
        r_price = float(row["sl_distance"])
        if quote is not None:
            bid, ask = quote
            entry = ask if direction > 0 else bid
        else:
            spread_est = inst.spread.price_units(float(row["close"]))
            entry = float(row["close"]) + (spread_est if direction > 0 else 0.0)
        stop = entry - direction * r_price
        tp1 = entry + direction * r_price * plan.targets_r[0]
        tp2 = entry + direction * r_price * plan.targets_r[1]
        # Zona de entrada hacia el lado bueno: comprar algo mas abajo (o vender
        # algo mas arriba) mejora el precio, pero la operacion puede no llegar.
        zone_depth = self.cfg.signals.entry_zone_r * r_price
        cost_price = inst.spread.price_units(entry) + 2 * inst.slippage.price_units(entry)

        history = self.artifacts.histories[plan_name]
        cases, scope = similar_cases(history, symbol, tf, direction, p1, self.cfg.signals.min_similar_cases)
        hits_tp1 = cases.loc[cases["hit_tp1"] == 1, "hours_to_tp1"]

        contributions = model.contributions(X.iloc[0]).sort_values()
        # nombre neutro ("RSI", no "RSI a favor"): el signo ya dice si en ESTA señal sube o baja la probabilidad
        top = [(feature_label(k).replace(" a favor", ""), float(v)) for k, v in contributions.items() if abs(v) >= 0.02]
        top_factors = sorted(top, key=lambda kv: -abs(kv[1]))[:7]

        votes_for = [v for v in view.votes if v.vote == direction]
        votes_against = [v for v in view.votes if v.vote == -direction]

        size = None
        if eurusd:
            try:
                size = position_size(capital_eur, risk_pct, r_price, inst, eurusd)
            except ValueError:
                size = None

        max_bars = plan.max_bars(self.cfg.timeframes[tf])
        signal = Signal(
            key=f"{symbol}|{tf}|{row['close_time'].isoformat()}",
            symbol=symbol,
            tf=tf,
            direction=direction,
            plan=plan_name,
            validated=validated,
            signal_time=row["close_time"],
            entry=entry,
            stop=stop,
            tp1=tp1,
            tp2=tp2,
            r_price=r_price,
            targets_r=(plan.targets_r[0], plan.targets_r[1]),
            partial=plan.partial,
            p_tp1=p1,
            p_tp2=p2,
            threshold=tau,
            ladder=probability_ladder(cases),
            similar_n=len(cases),
            similar_scope=scope,
            similar_ev=float(cases["realized_r"].mean()) if len(cases) else None,
            similar_tp1_rate=float(cases["hit_tp1"].mean()) if len(cases) else None,
            hours_median=float(cases["hours_to_exit"].median()) if len(cases) else None,
            hours_p75=float(cases["hours_to_exit"].quantile(0.75)) if len(cases) else None,
            hours_to_tp1_median=float(hits_tp1.median()) if len(hits_tp1) else None,
            max_hours=max_bars * TF_MINUTES[tf] / 60,
            triggers=[TRIGGER_NAMES[t] for t in row["triggers"]],
            votes_for=votes_for,
            votes_against=votes_against,
            top_factors=top_factors,
            news=news,
            live_quote=quote is not None,
            cost_r=cost_price / r_price if r_price > 0 else float("nan"),
            size=size,
            entry_low=entry - zone_depth if direction > 0 else entry,
            entry_high=entry if direction > 0 else entry + zone_depth,
            extra_target_r=self.cfg.signals.extra_target_r,
        )
        self._apply_gates(signal, mode, now)
        return signal

    def _apply_gates(self, s: Signal, mode: str, now: datetime) -> None:
        """Bloqueos en ambos modos: umbral del modelo, casos similares y
        noticias. Solo en modo estricto bloquean ademas "no validado" y
        "expectativa historica insuficiente"; en modo informativo pasan a ser
        avisos bien visibles (el proposito de ese modo es enseñar lo que ve)."""
        cfg = self.cfg.signals
        strict = mode == "strict"

        def strict_rule(message: str) -> None:
            (s.blockers if strict else s.warnings).append(message)

        if not s.validated:
            strict_rule("SIN VENTAJA VALIDADA: este instrumento/temporalidad no supero la validacion fuera de muestra")
        if s.similar_ev is None or s.similar_ev < cfg.min_expected_r:
            ev = "n/d" if s.similar_ev is None else f"{s.similar_ev:+.2f}R"
            strict_rule(f"expectativa historica de casos similares {ev} (minimo {cfg.min_expected_r:+.2f}R)")
        if s.p_tp1 < s.threshold:
            strict_rule(f"probabilidad TP1 {s.p_tp1:.1%} por debajo del umbral validado {s.threshold:.1%}")
        if s.similar_n < cfg.min_similar_cases:
            strict_rule(f"solo {s.similar_n} casos similares (minimo {cfg.min_similar_cases})")
        # Lo que sigue bloquea en AMBOS modos: no es cuestion de ventaja sino de
        # que la operacion sea ejecutable y segura tal como se describe.
        if cfg.min_probability_tp1 and s.p_tp1 < cfg.min_probability_tp1:
            s.blockers.append(f"probabilidad TP1 por debajo de tu minimo configurado ({cfg.min_probability_tp1:.1%})")
        blackout = cfg.news_blackout_hours.get(s.tf, 0)
        if blackout:
            soon = [e for e in s.news if e.time <= now + timedelta(hours=blackout)]
            if soon:
                s.blockers.append(
                    f"noticia de alto impacto en menos de {blackout:g}h ({soon[0].currency} {soon[0].title})"
                )
        age = now - s.signal_time.to_pydatetime()
        max_delay = MAX_EMIT_DELAY.get(s.tf, timedelta(minutes=30))
        if age > max_delay:
            minutes = int(age.total_seconds() // 60)
            s.blockers.append(
                f"llega tarde: la vela cerro hace {minutes} min (maximo {int(max_delay.total_seconds() // 60)} min "
                "para que sea la operacion que se valido)"
            )
        if s.size is not None and not s.size.fits:
            s.warnings.append(s.size.note)
        if not s.live_quote:
            s.warnings.append("Precio de entrada estimado con el ultimo cierre (sin cotizacion en vivo).")
