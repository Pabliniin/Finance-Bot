"""Mensajes de Discord (embeds).

Reglas de la casa:
- Todo texto que no escribimos nosotros (titulares de noticias) se escapa: no
  puede romper el formato ni colar menciones.
- Los limites de Discord se respetan siempre (campo 1024, 25 campos); si algo
  no cabe se recorta con "…" en vez de fallar el envio.
- Cada mensaje con numeros de rendimiento lleva el descargo.
- `to_text()` convierte cualquier embed en texto plano para la terminal, para
  que `python -m finance_bot scan` enseñe exactamente lo mismo que Discord.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import discord
import numpy as np
import pandas as pd

from finance_bot.config import AppConfig
from finance_bot.engine.exits import ExitAdvice
from finance_bot.engine.signals import Analysis, Signal, TimeframeView
from finance_bot.strategies.voters import FAMILIES, FAMILY_SHORT

GREEN = 0x2ECC71
RED = 0xE74C3C
AMBER = 0xF1C40F
BLUE = 0x3498DB
GREY = 0x95A5A6

FIELD_LIMIT = 1024
MAX_FIELDS = 25
_WEEKDAYS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
BIAS_ICON = {"alcista": "🟢", "bajista": "🔴", "neutral": "⚪"}


def esc(text: object) -> str:
    """Texto ajeno dentro de un mensaje nuestro."""
    return discord.utils.escape_markdown(str(text)).replace("@", "@​")


def price(value: float | None, digits: int) -> str:
    if value is None or not np.isfinite(value):
        return "n/d"
    return f"{value:,.{digits}f}"


def pct(value: float | None) -> str:
    return "n/d" if value is None or not np.isfinite(value) else f"{100 * value:.0f}%"


def hours_text(hours: float | None) -> str:
    if hours is None or not np.isfinite(hours):
        return "n/d"
    if hours < 1:
        return f"{hours * 60:.0f} min"
    if hours < 48:
        return f"{hours:.0f} h"
    return f"{hours / 24:.1f} dias"


def when(ts: datetime | str | pd.Timestamp) -> str:
    stamp = pd.Timestamp(ts)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return f"{_WEEKDAYS[stamp.dayofweek]} {stamp:%d/%m %H:%M} UTC"


def _add(embed: discord.Embed, name: str, value: str, inline: bool = False) -> discord.Embed:
    if not value or len(embed.fields) >= MAX_FIELDS:
        return embed
    if len(value) > FIELD_LIMIT:
        value = value[: FIELD_LIMIT - 1].rsplit("\n", 1)[0] + "…"
    embed.add_field(name=name[:256], value=value, inline=inline)
    return embed


def _disclaimer(embed: discord.Embed, cfg: AppConfig) -> discord.Embed:
    embed.set_footer(text=cfg.disclaimer[:2048])
    return embed


def _plain(text: str) -> str:
    """Sin el marcado de Discord, para leerlo comodo en una terminal."""
    text = re.sub(r"\\([*_~`>|-])", r"\1", text)  # deshace los escapes de escape_markdown
    return text.replace("**", "").replace("`", "")


def to_text(embed: discord.Embed) -> str:
    """El mismo contenido en texto plano (terminal)."""
    lines = [str(embed.title or "")]
    if embed.description:
        lines.append(embed.description)
    for field in embed.fields:
        lines.append(f"\n{field.name}\n{field.value}")
    if embed.footer and embed.footer.text:
        lines.append(f"\n{embed.footer.text}")
    return _plain("\n".join(line for line in lines if line))


# --- señal -------------------------------------------------------------------------


def _family_summary(votes: list, direction: int) -> str:
    parts = []
    for fam, label in FAMILIES.items():
        fam_votes = [v for v in votes if v.family == fam]
        if fam_votes:
            favor = sum(1 for v in fam_votes if v.vote == direction)
            parts.append(f"{label} {favor}/{len(fam_votes)}")
    return " · ".join(parts)


def _entry_zone(s: Signal, digits: int) -> str:
    """Zona de entrada: de la referencia (lo validado) hacia el lado bueno."""
    if s.entry_low is None or s.entry_high is None or s.entry_low >= s.entry_high:
        return f"`{price(s.entry, digits)}`"
    zone = f"`{price(s.entry_low, digits)} – {price(s.entry_high, digits)}`"
    better = "mas abajo" if s.direction > 0 else "mas arriba"
    return f"{zone}\nReferencia `{price(s.entry, digits)}`; {better} entras mejor, pero puede no llegar"


def _targets_block(s: Signal, digits: int) -> str:
    rows = []
    for label, target_price, r, probability in s.targets():
        extra = ""
        if label == "TP1":
            extra = f" · cierra {s.partial:.0%} y stop a la entrada"
        elif r > s.targets_r[1]:
            extra = " · extra, fuera del plan validado"
        chance = f" · llegaron {pct(probability)}" if probability is not None else ""
        rows.append(f"**{label}** `{price(target_price, digits)}` (+{r:g}R){chance}{extra}")
    rows.append(f"**SL** `{price(s.stop, digits)}` (−1R = {price(s.r_price, digits)})")
    return "\n".join(rows)


def signal_embed(s: Signal, cfg: AppConfig) -> discord.Embed:
    digits = cfg.instrument(s.symbol).digits
    icon = "🟢" if s.direction > 0 else "🔴"
    badge = "✅ validada fuera de muestra" if s.validated else "⚠️ **SIN VENTAJA VALIDADA** (modo informativo)"
    embed = discord.Embed(
        title=f"{icon} {s.side} · {s.symbol} · {s.tf}",
        description=f"{badge} · plan «{esc(s.plan)}»",
        color=GREEN if s.direction > 0 else RED,
        timestamp=datetime.now(UTC),
    )
    _add(embed, "💵 Zona de entrada", _entry_zone(s, digits) + ("" if s.live_quote else "\n(precio estimado)"))
    _add(embed, "🎯 Objetivos y stop", _targets_block(s, digits))
    _add(
        embed,
        "📊 Probabilidad de tocar TP1 antes que el stop",
        f"**{pct(s.p_tp1)}** segun el modelo (umbral validado {s.threshold:.1%})\n"
        f"**{pct(s.similar_tp1_rate)}** es lo que ocurrio en {s.similar_n} casos parecidos "
        f"({esc(s.similar_scope)})\n"
        f"TP2: {pct(s.p_tp2)} segun el modelo",
    )
    if s.similar_ev is not None:
        _add(embed, "💰 Expectativa", f"**{s.similar_ev:+.2f}R** por operacion en esos casos, ya con costes", True)
    duration = (
        f"Duracion tipica **{hours_text(s.hours_median)}** (3 de cada 4 cierran antes de {hours_text(s.hours_p75)})\n"
        f"Cierre por tiempo a las {hours_text(s.max_hours)}"
    )
    if s.hours_to_tp1_median is not None:
        duration += f"\nCuando toca TP1, suele tardar {hours_text(s.hours_to_tp1_median)}"
    _add(embed, "⏱ Cuanto suele durar", duration)
    favor = [f"• {esc(v.name)} — {esc(v.detail)}" for v in s.votes_for[:5]]
    _add(
        embed,
        f"✅ A favor ({len(s.votes_for)}/20)",
        (_family_summary(s.votes_for + s.votes_against, s.direction) + "\n" + "\n".join(favor)).strip(),
    )
    if s.votes_against:
        _add(
            embed,
            f"❌ En contra ({len(s.votes_against)})",
            "\n".join(f"• {esc(v.name)} — {esc(v.detail)}" for v in s.votes_against[:4]),
        )
    _add(embed, "🔎 Entrada que dispara", esc(", ".join(s.triggers)))
    raising = [name for name, v in s.top_factors if v > 0][:3]
    lowering = [name for name, v in s.top_factors if v < 0][:3]
    if raising or lowering:
        _add(
            embed,
            "🧠 Lo que mas pesa en la probabilidad",
            (f"Suben: {esc(', '.join(raising))}\n" if raising else "")
            + (f"Bajan: {esc(', '.join(lowering))}" if lowering else ""),
        )
    if s.size is not None:
        size = (
            f"**{s.size.lots:.2f} lotes** → riesgo {s.size.risk_eur:.2f} € ({s.size.risk_pct:.1f}%)"
            if s.size.fits
            else f"⚠️ {esc(s.size.note)}"
        )
        _add(embed, "💶 Tamaño para tu capital", size, True)
    _add(embed, "💸 Coste estimado", f"{s.cost_r:.2f}R (spread + deslizamiento)", True)
    if s.news:
        _add(
            embed,
            "📅 Noticias de alto impacto (72h)",
            "\n".join(f"• {esc(e.currency)} {esc(e.title)} — {when(e.time)}" for e in s.news[:4]),
        )
    for warning in s.warnings:
        _add(embed, "⚠️ Aviso", esc(warning))
    return _disclaimer(embed, cfg)


# --- analisis ----------------------------------------------------------------------


def _view_value(v: TimeframeView, digits: int) -> str:
    fam = []
    for key, label in FAMILY_SHORT.items():
        votes = [x.vote for x in v.votes if x.family == key]
        if votes:
            fam.append(f"{label} {votes.count(1)}↑{votes.count(-1)}↓")
    text = (
        f"RSI {v.rsi:.0f} · ADX {v.adx:.0f} · ATR {price(v.atr, digits)}\n"
        f"soporte {price(v.support, digits)} · resistencia {price(v.resistance, digits)}\n"
        f"{esc(' · '.join(fam))}\nvela cerrada {when(v.bar_close)}"
    )
    if v.triggers_long or v.triggers_short:
        trig = [f"{t} (compra)" for t in v.triggers_long] + [f"{t} (venta)" for t in v.triggers_short]
        text += f"\n🔎 Entradas activas: {esc(', '.join(trig))}"
    return text


def analysis_embed(a: Analysis, cfg: AppConfig) -> discord.Embed:
    digits = cfg.instrument(a.symbol).digits
    total = sum(v.score for v in a.views.values())
    color = GREEN if total >= 4 else RED if total <= -4 else GREY
    header = f"Precio {price(a.quote[0], digits)} / {price(a.quote[1], digits)} (bid/ask)\n" if a.quote else ""
    embed = discord.Embed(
        title=f"📈 {a.symbol} — todo lo que ve el bot",
        description=f"{header}Datos hasta "
        f"{when(a.data_until) if a.data_until is not None else 'n/d'} · fuente {esc(a.feed)}",
        color=color,
        timestamp=datetime.now(UTC),
    )
    for tf in ("D1", "H4", "H1", "M15"):
        if tf in a.views:
            v = a.views[tf]
            _add(embed, f"{BIAS_ICON[v.bias]} {tf} · sesgo {v.bias} ({v.score:+d}/20)", _view_value(v, digits))
    if a.signals:
        lines = []
        for s in a.signals:
            status = "✅ cumple todo → señal" if s.emit else "⛔ " + "; ".join(s.blockers)
            lines.append(
                f"**{s.side} {s.tf}** · prob. TP1 {pct(s.p_tp1)} · casos parecidos "
                f"{'n/d' if s.similar_ev is None else f'{s.similar_ev:+.2f}R'}\n{esc(status)}"
            )
        _add(embed, "🎯 Setups en la ultima vela cerrada", "\n\n".join(lines))
    else:
        _add(embed, "🎯 Setups", "Ninguna estrategia de entrada dispara en la ultima vela cerrada.")
    if a.news:
        _add(
            embed,
            "📅 Noticias de alto impacto",
            "\n".join(f"• {esc(e.currency)} {esc(e.title)} — {when(e.time)}" for e in a.news[:5]),
        )
    for warning in a.warnings:
        _add(embed, "⚠️ Aviso", esc(warning))
    return _disclaimer(embed, cfg)


# --- gestion de una señal abierta --------------------------------------------------


def advice_embed(advice: ExitAdvice, cfg: AppConfig) -> discord.Embed:
    icon = "🚨" if advice.urgency == "alta" else "⏳"
    embed = discord.Embed(
        title=f"{icon} Vigila tu {advice.side} de {advice.symbol} {advice.tf}",
        description=f"**{esc(advice.headline)}**\n{esc(advice.detail)}",
        color=RED if advice.urgency == "alta" else AMBER,
        timestamp=datetime.now(UTC),
    )
    if advice.r_now is not None:
        _add(embed, "Vas en", f"**{advice.r_now:+.2f}R** si cierras ahora", True)
    _add(
        embed,
        "Que es esto",
        "Aviso de contexto, **no** forma parte de lo validado: el backtest mantiene hasta stop, objetivo o cierre "
        "por tiempo. Queda guardado con este R para compararlo despues con el resultado real (`/stats`). Tu decides.",
    )
    return _disclaimer(embed, cfg)


def event_embed(event: dict, cfg: AppConfig) -> discord.Embed:
    symbol, tf = event["symbol"], event["tf"]
    side = "COMPRA" if event["direction"] > 0 else "VENTA"
    if event["type"] == "tp1":
        return _disclaimer(
            discord.Embed(
                title=f"🎯 TP1 alcanzado · {side} {symbol} {tf}",
                description="Segun el plan: cierra la mitad y mueve el stop a la entrada.\n"
                f"Ha tardado {hours_text(event.get('hours'))}.",
                color=GREEN,
                timestamp=datetime.now(UTC),
            ),
            cfg,
        )
    reasons = {
        "stop": ("🛑 Stop", RED),
        "tp2": ("🏁 TP2 alcanzado", GREEN),
        "tp1_be": ("🤝 Cerrada en la entrada tras TP1", AMBER),
        "time": ("⌛ Cerrada por tiempo", GREY),
    }
    title, color = reasons.get(str(event.get("reason")), ("🔚 Cerrada", GREY))
    r = float(event.get("realized_r", 0.0))
    embed = discord.Embed(
        title=f"{title} · {side} {symbol} {tf}",
        description=f"Resultado siguiendo el plan: **{r:+.2f}R** en {hours_text(event.get('hours'))}.",
        color=color if r >= 0 else RED,
        timestamp=datetime.now(UTC),
    )
    advice_r = event.get("advice_r")
    if advice_r is not None:
        diff = float(advice_r) - r
        verdict = "habria sido mejor" if diff > 0.05 else ("habria dado igual" if abs(diff) <= 0.05 else "fue peor")
        _add(
            embed,
            "El aviso de gestion, a toro pasado",
            f"Aviso: {esc(event.get('advice_headline') or '')} con **{float(advice_r):+.2f}R**.\n"
            f"Cerrar ahi {verdict} ({diff:+.2f}R frente al plan).",
        )
    return _disclaimer(embed, cfg)


# --- paneles y resumenes -----------------------------------------------------------


def _bias_line(a: Analysis) -> str:
    parts = [
        f"{tf} {BIAS_ICON[a.views[tf].bias]} {a.views[tf].score:+d}"
        for tf in ("D1", "H4", "H1", "M15")
        if tf in a.views
    ]
    return " · ".join(parts) if parts else "sin datos"


def panel_embed(
    analyses: dict[str, Analysis],
    open_signals: pd.DataFrame,
    health: dict,
    cfg: AppConfig,
    r_by_key: dict[str, float] | None = None,
) -> discord.Embed:
    """El panel fijo del canal: de un vistazo, como esta todo ahora mismo."""
    ks_active, ks_reason = health["kill_switch"]
    mode = "estricto" if health["mode"] == "strict" else "informativo"
    embed = discord.Embed(
        title="🧭 Panel — XAUUSD y EURUSD",
        description=f"Modo **{mode}** · fuente {esc(health['feed'])} · "
        f"ultimo escaneo {when(health['last_scan']) if health['last_scan'] else 'aun ninguno'}",
        color=RED if ks_active else BLUE,
        timestamp=datetime.now(UTC),
    )
    for symbol, a in analyses.items():
        digits = cfg.instrument(symbol).digits
        header = f"Precio {price(a.quote[0], digits)}\n" if a.quote else ""
        line = (
            "sin entradas activas"
            if not a.signals
            else "\n".join(
                f"{'✅' if s.emit else '⛔'} {s.side} {s.tf} · prob. TP1 {pct(s.p_tp1)}" for s in a.signals[:3]
            )
        )
        _add(embed, f"{symbol}", f"{header}{_bias_line(a)}\n{line}", True)
    if not open_signals.empty:
        rows = []
        for _, s in open_signals.iterrows():
            digits = cfg.instrument(s["symbol"]).digits
            side = "COMPRA" if s["direction"] > 0 else "VENTA"
            now_r = (r_by_key or {}).get(s["key"])
            extra = f" · ahora **{now_r:+.2f}R**" if now_r is not None else ""
            tp1 = " · TP1 ✅" if s["tp1_notified"] else ""
            rows.append(f"• {side} {s['symbol']} {s['tf']} desde {price(s['entry'], digits)}{extra}{tp1}")
        _add(embed, f"📌 Señales abiertas ({len(open_signals)})", "\n".join(rows))
    else:
        _add(embed, "📌 Señales abiertas", "Ninguna.")
    if ks_active:
        _add(embed, "🔴 Interruptor de seguridad ACTIVO", f"{esc(ks_reason)}\nNo se emiten señales. Usa `/reactivar`.")
    _add(embed, "Comandos", "`/analisis` `/senales` `/stats` `/validacion` `/riesgo` `/estado` `/ayuda`")
    return embed


def open_signals_embed(
    open_signals: pd.DataFrame, cfg: AppConfig, r_by_key: dict[str, float] | None = None
) -> discord.Embed:
    if open_signals.empty:
        return discord.Embed(
            title="📌 Señales abiertas",
            description="Ninguna. Te aviso en cuanto un setup cumpla todas las condiciones.",
            color=GREY,
        )
    embed = discord.Embed(title=f"📌 Señales abiertas ({len(open_signals)})", color=BLUE, timestamp=datetime.now(UTC))
    for _, s in open_signals.iterrows():
        digits = cfg.instrument(s["symbol"]).digits
        side = "COMPRA" if s["direction"] > 0 else "VENTA"
        now_r = (r_by_key or {}).get(s["key"])
        value = (
            f"entrada `{price(s['entry'], digits)}` · stop `{price(s['stop'], digits)}`\n"
            f"TP1 `{price(s['tp1'], digits)}` · TP2 `{price(s['tp2'], digits)}`\n"
            f"prob. TP1 {pct(s['p_tp1'])} · abierta desde {when(s['signal_time'])}"
        )
        if now_r is not None:
            value += f"\nAhora mismo: **{now_r:+.2f}R**"
        if s["tp1_notified"]:
            value += "\n✅ TP1 tocado: mitad cerrada y stop en la entrada"
        _add(embed, f"{'🟢' if s['direction'] > 0 else '🔴'} {side} {s['symbol']} {s['tf']}", value)
    return _disclaimer(embed, cfg)


def stats_embed(closed: pd.DataFrame, scoreboard: pd.DataFrame, period_label: str, cfg: AppConfig) -> discord.Embed:
    if closed.empty:
        embed = discord.Embed(
            title=f"📊 Resultados reales — {period_label}",
            description="Sin señales cerradas en este periodo todavia.",
            color=GREY,
        )
        return _disclaimer(embed, cfg)
    r = closed["realized_r"].astype(float)
    equity = r.cumsum()
    drawdown = float((equity.cummax().clip(lower=0) - equity).max())
    embed = discord.Embed(
        title=f"📊 Resultados reales — {period_label}",
        description=f"Seguimiento en papel de {len(closed)} señales cerradas, con las reglas del plan.",
        color=GREEN if r.mean() > 0 else RED,
        timestamp=datetime.now(UTC),
    )
    _add(embed, "Expectativa", f"**{r.mean():+.2f}R** por señal\nTotal {r.sum():+.1f}R", True)
    _add(
        embed,
        "Acierto",
        f"TP1 {pct(closed['hit_tp1'].mean())} (el modelo predijo {pct(closed['p_tp1'].astype(float).mean())})\n"
        f"TP2 {pct(closed['hit_tp2'].mean())}\nGanadoras {pct((r > 0).mean())}",
        True,
    )
    _add(embed, "Peor racha", f"{drawdown:.1f}R", True)
    by_group = closed.groupby(["symbol", "tf"])["realized_r"].agg(["count", "mean"])
    _add(
        embed,
        "Por instrumento y temporalidad",
        "\n".join(
            f"• {sym} {tf}: {int(row['count'])} señales, {row['mean']:+.2f}R" for (sym, tf), row in by_group.iterrows()
        ),
    )
    if not scoreboard.empty:
        diff = (scoreboard["r_at_advice"].astype(float) - scoreboard["realized_r"].astype(float)).mean()
        verdict = (
            "habrian ayudado" if diff > 0.05 else ("habrian dado igual" if abs(diff) <= 0.05 else "habrian restado")
        )
        _add(
            embed,
            "🚨 Avisos de gestion, a toro pasado",
            f"{len(scoreboard)} señales con aviso.\n"
            f"Cerrar en el aviso: **{scoreboard['r_at_advice'].astype(float).mean():+.2f}R** de media.\n"
            f"Seguir el plan: **{scoreboard['realized_r'].astype(float).mean():+.2f}R**.\n"
            f"Es decir, {verdict} ({diff:+.2f}R).",
        )
    if len(closed) < 30:
        _add(
            embed,
            "⚠️ Ojo con estos numeros",
            f"Con {len(closed)} señales cualquier cifra es sobre todo ruido. Hacen falta decenas para juzgar nada.",
        )
    return _disclaimer(embed, cfg)


def validation_embed(validation: dict | None, cfg: AppConfig) -> discord.Embed:
    if not validation:
        return discord.Embed(
            title="🧪 Validacion",
            description="Aun no hay validacion. Ejecuta en el PC: `python -m finance_bot research`",
            color=AMBER,
        )
    selection = validation.get("selection", {})
    embed = discord.Embed(
        title="🧪 Validacion fuera de muestra",
        description=f"Generada {esc(str(validation.get('generated_at', 'n/d'))[:16])} · test reservado evaluado: "
        f"{'si' if validation.get('test_evaluated') else 'no'}",
        color=GREEN if selection else AMBER,
    )
    if selection:
        _add(
            embed,
            "✅ Con ventaja validada (emiten en modo estricto)",
            "\n".join(f"• {esc(k)} → plan «{esc(v)}»" for k, v in sorted(selection.items())),
        )
    else:
        _add(
            embed,
            "Ninguna combinacion supero la validacion",
            "Despues de costes, ninguna combinacion de instrumento, temporalidad y plan demostro ventaja. "
            "En modo estricto el bot NO emite señales de entrada; `/analisis` sigue enseñando todo lo que ve.",
        )
    for plan, info in validation.get("plans", {}).items():
        rows = []
        for key, g in sorted(info.get("groups", {}).items()):
            summary = g.get("summary", {})
            if not summary or not summary.get("n"):
                continue
            mean_r = summary.get("mean_r")
            rows.append(
                f"• {esc(key)}: {summary['n']} señales, TP1 {pct(summary.get('tp1_rate'))}, "
                f"{'n/d' if mean_r is None else f'{mean_r:+.3f}R'}{' ✅' if g.get('eligible') else ''}"
            )
        if rows:
            _add(embed, f"Plan {plan} · umbral {pct(info.get('threshold_tp1'))}", "\n".join(rows))
    embed.set_footer(text="Informe completo: docs/validacion_2026-09-22.md y reports/latest.md en el PC del bot.")
    return embed


def status_embed(health: dict, cfg: AppConfig) -> discord.Embed:
    ks_active, ks_reason = health["kill_switch"]
    embed = discord.Embed(
        title="🩺 Estado del sistema",
        color=RED if ks_active or health.get("last_error") else GREEN,
        timestamp=datetime.now(UTC),
    )
    _add(embed, "Fuente de datos", esc(health["feed"]), True)
    _add(embed, "Ultimo escaneo", when(health["last_scan"]) if health["last_scan"] else "aun ninguno", True)
    _add(embed, "Modo", ("estricto" if health["mode"] == "strict" else "informativo"), True)
    _add(embed, "Señales abiertas", str(health["open_signals"]), True)
    _add(
        embed,
        "Interruptor de seguridad",
        ("🔴 ACTIVO — " + esc(ks_reason) if ks_active else "🟢 inactivo"),
        True,
    )
    _add(embed, "Calendario economico", "OK" if health["calendar_ok"] else "⚠️ no disponible", True)
    model = health.get("model")
    _add(
        embed,
        "Modelo",
        f"entrenado {esc(str(model['trained_at'])[:16])}"
        if model
        else "⚠️ sin entrenar (`python -m finance_bot research`)",
    )
    _add(
        embed,
        "Datos",
        "\n".join(
            f"• {symbol}: completos hasta {when(d['complete_until']) if d['complete_until'] is not None else 'n/d'}"
            for symbol, d in health["data"].items()
        ),
    )
    if health.get("last_error"):
        _add(embed, "⚠️ Ultimo error", esc(health["last_error"]))
    return embed


def calendar_embed(events: list, cfg: AppConfig, failed: bool = False) -> discord.Embed:
    if failed:
        return discord.Embed(
            title="📅 Calendario economico",
            description="⚠️ El feed gratuito no responde ahora mismo. No invento que no haya noticias: vuelve a "
            "intentarlo en unos minutos.",
            color=AMBER,
        )
    if not events:
        return discord.Embed(
            title="📅 Calendario economico",
            description="Sin noticias de alto impacto USD/EUR en los proximos dias.",
            color=GREY,
        )
    embed = discord.Embed(title="📅 Alto impacto USD/EUR", color=BLUE)
    for event in events[:20]:
        _add(
            embed,
            f"{esc(event.currency)} · {when(event.time)}",
            esc(event.title) + (f"\nPrevision: {esc(event.forecast)}" if event.forecast else ""),
            True,
        )
    return embed


def help_embed(cfg: AppConfig, validated: int, model_ready: bool) -> discord.Embed:
    embed = discord.Embed(
        title="🤖 Como funciona este bot",
        description=(
            f"Vigila **{' y '.join(cfg.instruments)}** en M15, H1, H4 y D1, siempre sobre velas ya cerradas.\n"
            "20 estrategias votan, 8 estrategias buscan el momento de entrada y un modelo calibrado con "
            "historico fuera de muestra estima la probabilidad real de cada objetivo."
        ),
        color=BLUE,
    )
    _add(
        embed,
        "Cuando te avisa",
        "Solo si se cumple TODO: combinacion validada, probabilidad sobre el umbral, expectativa positiva en casos "
        "parecidos, casos suficientes, sin noticia fuerte encima y aviso a tiempo.",
    )
    _add(
        embed,
        "Mientras la operacion vive",
        "Te avisa al tocar TP1 (cierra la mitad y stop a la entrada), al cerrarse, y **si ve motivo para cerrar "
        "antes**: giro del contexto, entrada contraria, noticia encima, se acaba el tiempo o estas devolviendo "
        "beneficio. Esos avisos son contexto, no forman parte de lo validado.",
    )
    _add(
        embed,
        "Comandos",
        "`/analisis` todo lo que ve · `/senales` abiertas · `/stats` resultados reales\n"
        "`/validacion` que tiene ventaja demostrada · `/riesgo` calculadora de lotes · `/calendario` noticias\n"
        "`/capital` `/riesgo_pct` `/modo` `/silenciar` `/estado` `/reactivar` `/panel`",
    )
    if not model_ready:
        _add(embed, "⚠️ Sin modelo", "Ejecuta `python -m finance_bot research` en el PC del bot.")
    elif validated == 0:
        _add(
            embed,
            "⚠️ Lo que debes saber",
            "Ninguna combinacion supero la validacion fuera de muestra: despues de costes no se encontro ventaja. "
            "En modo estricto NO recibiras señales de entrada. `/validacion` tiene los numeros y `/modo informativo` "
            "enseña los setups marcados como no validados.",
        )
    return _disclaimer(embed, cfg)


def simple_embed(title: str, description: str, color: int = BLUE) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color)
