"""Mensajes de Discord (embeds).

Criterio: que se lean de un vistazo en el movil. Poco texto, los numeros que
importan en bloque monoespaciado y nada de parrafos largos.

Reglas de la casa:
- Todo texto que no escribimos nosotros (titulares de noticias) se escapa: no
  puede romper el formato ni colar menciones.
- Los limites de Discord se respetan siempre (campo 1024, 25 campos); si algo
  no cabe se recorta con "…" en vez de fallar el envio.
- Cada mensaje con numeros de rendimiento lleva el descargo, corto pero claro.
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
from finance_bot.strategies.voters import FAMILY_SHORT

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


def clock(ts: datetime | str | pd.Timestamp | None) -> str:
    if ts is None:
        return "n/d"
    stamp = pd.Timestamp(ts)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return f"{stamp:%H:%M} UTC"


def _add(embed: discord.Embed, name: str, value: str, inline: bool = False) -> discord.Embed:
    if not value or len(embed.fields) >= MAX_FIELDS:
        return embed
    if len(value) > FIELD_LIMIT:
        value = value[: FIELD_LIMIT - 1].rsplit("\n", 1)[0] + "…"
    embed.add_field(name=name[:256], value=value, inline=inline)
    return embed


def _disclaimer(embed: discord.Embed, cfg: AppConfig) -> discord.Embed:
    embed.set_footer(text=cfg.disclaimer_short[:2048])
    return embed


def _plain(text: str) -> str:
    """Sin el marcado de Discord, para leerlo comodo en una terminal."""
    text = re.sub(r"\\([*_~`>|-])", r"\1", text)  # deshace los escapes de escape_markdown
    return text.replace("**", "").replace("```", "").replace("`", "")


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


def _plan_block(s: Signal, digits: int) -> str:
    """El plan entero en un bloque monoespaciado: se lee de una ojeada."""
    width = max(len(price(v, digits)) for v in (s.entry, s.stop, s.tp2, s.entry_low or s.entry))
    rows = []
    if s.entry_low is not None and s.entry_high is not None and s.entry_low < s.entry_high:
        zone = f"{price(s.entry_low, digits)} – {price(s.entry_high, digits)}"
        rows.append(f"Zona  {zone}")
    else:
        rows.append(f"Zona  {price(s.entry, digits):>{width}}")
    rows.append(f"Ref.  {price(s.entry, digits):>{width}}   entrada validada")
    rows.append(f"SL    {price(s.stop, digits):>{width}}   -1R = {price(s.r_price, digits)}")
    for label, target_price, r, probability in s.targets():
        note = f"llego {pct(probability)}" if probability is not None else ""
        if label == "TP1":
            note += f" · cierra {s.partial:.0%}, stop a entrada"
        elif r > s.targets_r[1]:
            note += " · extra, no validado"
        rows.append(f"{label}   {price(target_price, digits):>{width}}  +{r:g}R  {note}".rstrip())
    return "```\n" + "\n".join(rows) + "\n```"


def signal_embed(s: Signal, cfg: AppConfig) -> discord.Embed:
    digits = cfg.instrument(s.symbol).digits
    icon = "🟢" if s.direction > 0 else "🔴"
    badge = "✅ validada" if s.validated else "⚠️ **SIN VENTAJA VALIDADA**"
    header = f"{badge} · plan «{esc(s.plan)}» · {esc(s.tf)}"
    if not s.live_quote:
        header += " · precio estimado"
    embed = discord.Embed(
        title=f"{icon} {s.side} · {s.symbol}",
        description=f"{header}\n{_plan_block(s, digits)}",
        color=GREEN if s.direction > 0 else RED,
        timestamp=datetime.now(UTC),
    )
    prob = (
        f"TP1 antes que el stop: **{pct(s.p_tp1)}** segun el modelo · "
        f"**{pct(s.similar_tp1_rate)}** en {s.similar_n} casos parecidos"
    )
    if s.similar_ev is not None:
        prob += f"\nExpectativa de esos casos, con costes: **{s.similar_ev:+.2f}R**"
    _add(embed, "📊 Probabilidad", prob)
    _add(
        embed,
        "⏱ Duracion",
        f"tipica **{hours_text(s.hours_median)}**\ncierre por tiempo {hours_text(s.max_hours)}",
        True,
    )
    if s.size is not None:
        size = (
            f"**{s.size.lots:.2f} lotes**\n{s.size.risk_eur:.2f} € ({s.size.risk_pct:.1f}%)"
            if s.size.fits
            else f"⚠️ {esc(s.size.note)}"
        )
        _add(embed, "💶 Tamaño", size, True)
    fam = _family_line(s.votes_for + s.votes_against, s.direction)
    _add(
        embed,
        f"🧭 Por que ({len(s.votes_for)}/20 a favor)",
        f"{esc(fam)}\nDispara: {esc(', '.join(s.triggers))}",
    )
    if s.news:
        _add(
            embed,
            "📅 Noticias",
            "\n".join(f"{esc(e.currency)} {esc(e.title)} — {when(e.time)}" for e in s.news[:3]),
        )
    if s.warnings:
        _add(embed, "⚠️ Ojo", "\n".join(f"• {esc(w)}" for w in s.warnings[:3]))
    return _disclaimer(embed, cfg)


def _family_line(votes: list, direction: int) -> str:
    parts = []
    for key, label in FAMILY_SHORT.items():
        family_votes = [v for v in votes if v.family == key]
        if family_votes:
            favor = sum(1 for v in family_votes if v.vote == direction)
            parts.append(f"{label} {favor}/{len(family_votes)}")
    return " · ".join(parts)


# --- analisis ----------------------------------------------------------------------


def _view_line(v: TimeframeView, digits: int) -> str:
    line = f"{BIAS_ICON[v.bias]} `{v.tf:<3}` **{v.score:+d}**/20 · RSI {v.rsi:.0f} · ADX {v.adx:.0f}"
    if v.support is not None or v.resistance is not None:
        line += f" · sop {price(v.support, digits)} / res {price(v.resistance, digits)}"
    if v.triggers_long or v.triggers_short:
        trig = [f"{t} (compra)" for t in v.triggers_long] + [f"{t} (venta)" for t in v.triggers_short]
        line += f"\n   🔎 {esc(', '.join(trig))}"
    return line


def analysis_embed(a: Analysis, cfg: AppConfig) -> discord.Embed:
    digits = cfg.instrument(a.symbol).digits
    total = sum(v.score for v in a.views.values())
    color = GREEN if total >= 4 else RED if total <= -4 else GREY
    head = f"**{price(a.quote[0], digits)}** · " if a.quote else ""
    embed = discord.Embed(
        title=f"📈 {a.symbol}",
        description=f"{head}datos hasta {clock(a.data_until)} · {esc(a.feed)}",
        color=color,
        timestamp=datetime.now(UTC),
    )
    lines = [_view_line(a.views[tf], digits) for tf in ("D1", "H4", "H1", "M15") if tf in a.views]
    _add(embed, "Temporalidades", "\n".join(lines))
    if a.signals:
        rows = []
        for s in a.signals:
            status = "✅ cumple todo" if s.emit else "⛔ " + "; ".join(s.blockers)
            rows.append(
                f"**{s.side} {s.tf}** · TP1 {pct(s.p_tp1)} · "
                f"{'n/d' if s.similar_ev is None else f'{s.similar_ev:+.2f}R'}\n{esc(status)}"
            )
        _add(embed, "🎯 Setups", "\n".join(rows))
    else:
        _add(embed, "🎯 Setups", "Ninguna entrada dispara en la ultima vela cerrada.")
    if a.news:
        _add(embed, "📅 Noticias", "\n".join(f"{esc(e.currency)} {esc(e.title)} — {when(e.time)}" for e in a.news[:3]))
    if a.warnings:
        _add(embed, "⚠️ Ojo", "\n".join(f"• {esc(w)}" for w in a.warnings[:3]))
    return _disclaimer(embed, cfg)


# --- gestion de una señal abierta --------------------------------------------------


def advice_embed(advice: ExitAdvice, cfg: AppConfig) -> discord.Embed:
    icon = "🚨" if advice.urgency == "alta" else "⏳"
    now_r = f" · vas **{advice.r_now:+.2f}R**" if advice.r_now is not None else ""
    embed = discord.Embed(
        title=f"{icon} {advice.side} {advice.symbol} {advice.tf} — {advice.headline}",
        description=f"{esc(advice.detail)}{now_r}",
        color=RED if advice.urgency == "alta" else AMBER,
        timestamp=datetime.now(UTC),
    )
    embed.set_footer(text="Aviso de contexto, no validado: el plan aguanta hasta stop, objetivo o tiempo. Tu decides.")
    return embed


def event_embed(event: dict, cfg: AppConfig) -> discord.Embed:
    symbol, tf = event["symbol"], event["tf"]
    side = "COMPRA" if event["direction"] > 0 else "VENTA"
    if event["type"] == "tp1":
        embed = discord.Embed(
            title=f"🎯 TP1 · {side} {symbol} {tf}",
            description=f"Cierra {event.get('partial', 0.5):.0%} y mueve el stop a la entrada. "
            f"Tardo {hours_text(event.get('hours'))}.",
            color=GREEN,
            timestamp=datetime.now(UTC),
        )
        return _disclaimer(embed, cfg)
    reasons = {
        "stop": ("🛑 Stop", RED),
        "tp2": ("🏁 TP2", GREEN),
        "tp1_be": ("🤝 Cerrada en la entrada tras TP1", AMBER),
        "time": ("⌛ Cerrada por tiempo", GREY),
    }
    title, color = reasons.get(str(event.get("reason")), ("🔚 Cerrada", GREY))
    r = float(event.get("realized_r", 0.0))
    embed = discord.Embed(
        title=f"{title} · {side} {symbol} {tf} · {r:+.2f}R",
        description=f"Siguiendo el plan, en {hours_text(event.get('hours'))}.",
        color=color if r >= 0 else RED,
        timestamp=datetime.now(UTC),
    )
    advice_r = event.get("advice_r")
    if advice_r is not None:
        diff = float(advice_r) - r
        verdict = "habria sido mejor" if diff > 0.05 else ("habria dado igual" if abs(diff) <= 0.05 else "fue peor")
        _add(
            embed,
            "El aviso, a toro pasado",
            f"Avise con **{float(advice_r):+.2f}R**: cerrar ahi {verdict} ({diff:+.2f}R).",
        )
    return _disclaimer(embed, cfg)


# --- paneles y resumenes -----------------------------------------------------------


def _bias_line(a: Analysis) -> str:
    parts = [
        f"{tf} {BIAS_ICON[a.views[tf].bias]}{a.views[tf].score:+d}" for tf in ("D1", "H4", "H1", "M15") if tf in a.views
    ]
    return " · ".join(parts) if parts else "sin datos"


def panel_embed(
    analyses: dict[str, Analysis],
    open_signals: pd.DataFrame,
    health: dict,
    cfg: AppConfig,
    r_by_key: dict[str, float] | None = None,
) -> discord.Embed:
    """El panel fijo del canal: como esta todo ahora mismo, de un vistazo."""
    ks_active, ks_reason = health["kill_switch"]
    mode = "estricto" if health["mode"] == "strict" else "informativo"
    embed = discord.Embed(
        title="🧭 Panel",
        description=f"{clock(health['last_scan'])} · modo {mode} · {esc(health['feed'])}",
        color=RED if ks_active else BLUE,
        timestamp=datetime.now(UTC),
    )
    for symbol, a in analyses.items():
        digits = cfg.instrument(symbol).digits
        head = f"**{price(a.quote[0], digits)}**\n" if a.quote else ""
        setups = [f"{'✅' if s.emit else '⛔'} {s.side} {s.tf} · {pct(s.p_tp1)}" for s in a.signals[:2]]
        body = "\n".join(setups) if setups else "sin entradas"
        _add(embed, symbol, f"{head}{_bias_line(a)}\n{body}", True)
    if not open_signals.empty:
        rows = []
        for _, s in open_signals.iterrows():
            digits = cfg.instrument(s["symbol"]).digits
            side = "COMPRA" if s["direction"] > 0 else "VENTA"
            now_r = (r_by_key or {}).get(s["key"])
            extra = f" · **{now_r:+.2f}R**" if now_r is not None else ""
            tp1 = " · TP1 ✅" if s["tp1_notified"] else ""
            rows.append(f"{side} {s['symbol']} {s['tf']} desde {price(s['entry'], digits)}{extra}{tp1}")
        _add(embed, f"📌 Abiertas ({len(open_signals)})", "\n".join(rows))
    else:
        _add(embed, "📌 Abiertas", "Ninguna.")
    if ks_active:
        _add(embed, "🔴 Interruptor de seguridad", f"{esc(ks_reason)}\nNo se emiten señales. `/reactivar`.")
    return embed


def open_signals_embed(
    open_signals: pd.DataFrame, cfg: AppConfig, r_by_key: dict[str, float] | None = None
) -> discord.Embed:
    if open_signals.empty:
        return discord.Embed(
            title="📌 Sin señales abiertas",
            description="Te aviso en cuanto un setup cumpla todas las condiciones.",
            color=GREY,
        )
    embed = discord.Embed(title=f"📌 Abiertas ({len(open_signals)})", color=BLUE, timestamp=datetime.now(UTC))
    for _, s in open_signals.iterrows():
        digits = cfg.instrument(s["symbol"]).digits
        side = "COMPRA" if s["direction"] > 0 else "VENTA"
        now_r = (r_by_key or {}).get(s["key"])
        value = (
            f"```\nEntrada {price(s['entry'], digits)}\n"
            f"SL      {price(s['stop'], digits)}\n"
            f"TP1     {price(s['tp1'], digits)}\n"
            f"TP2     {price(s['tp2'], digits)}\n```"
        )
        extra = f"**{now_r:+.2f}R** ahora · " if now_r is not None else ""
        extra += f"TP1 {pct(s['p_tp1'])} · desde {when(s['signal_time'])}"
        if s["tp1_notified"]:
            extra += "\n✅ TP1 tocado: mitad cerrada, stop en la entrada"
        _add(embed, f"{'🟢' if s['direction'] > 0 else '🔴'} {side} {s['symbol']} {s['tf']}", value + extra)
    return _disclaimer(embed, cfg)


def stats_embed(closed: pd.DataFrame, scoreboard: pd.DataFrame, period_label: str, cfg: AppConfig) -> discord.Embed:
    if closed.empty:
        return _disclaimer(
            discord.Embed(
                title=f"📊 Resultados — {period_label}",
                description="Sin señales cerradas en este periodo todavia.",
                color=GREY,
            ),
            cfg,
        )
    r = closed["realized_r"].astype(float)
    equity = r.cumsum()
    drawdown = float((equity.cummax().clip(lower=0) - equity).max())
    embed = discord.Embed(
        title=f"📊 Resultados — {period_label}",
        description=f"**{r.mean():+.2f}R** por señal · total {r.sum():+.1f}R · {len(closed)} cerradas "
        f"(seguimiento en papel)",
        color=GREEN if r.mean() > 0 else RED,
        timestamp=datetime.now(UTC),
    )
    _add(
        embed,
        "Acierto",
        f"TP1 {pct(closed['hit_tp1'].mean())} (modelo: {pct(closed['p_tp1'].astype(float).mean())})\n"
        f"TP2 {pct(closed['hit_tp2'].mean())} · ganadoras {pct((r > 0).mean())} · peor racha {drawdown:.1f}R",
    )
    by_group = closed.groupby(["symbol", "tf"])["realized_r"].agg(["count", "mean"])
    _add(
        embed,
        "Por grupo",
        "\n".join(f"{sym} {tf}: {int(row['count'])} · {row['mean']:+.2f}R" for (sym, tf), row in by_group.iterrows()),
    )
    if not scoreboard.empty:
        advice_r = scoreboard["r_at_advice"].astype(float).mean()
        plan_r = scoreboard["realized_r"].astype(float).mean()
        diff = advice_r - plan_r
        verdict = "ayudan" if diff > 0.05 else ("dan igual" if abs(diff) <= 0.05 else "restan")
        _add(
            embed,
            "🚨 Avisos de cierre",
            f"{len(scoreboard)} con aviso · cerrar ahi {advice_r:+.2f}R vs plan {plan_r:+.2f}R → **{verdict}**",
        )
    if len(closed) < 30:
        _add(embed, "⚠️ Ojo", f"Con {len(closed)} señales esto es sobre todo ruido.")
    return _disclaimer(embed, cfg)


def validation_embed(validation: dict | None, cfg: AppConfig) -> discord.Embed:
    if not validation:
        return discord.Embed(
            title="🧪 Sin validacion",
            description="Ejecuta en el PC del bot: `python -m finance_bot research`",
            color=AMBER,
        )
    selection = validation.get("selection", {})
    embed = discord.Embed(
        title="🧪 Validacion fuera de muestra",
        description=f"{esc(str(validation.get('generated_at', 'n/d'))[:10])} · test reservado evaluado: "
        f"{'si' if validation.get('test_evaluated') else 'no'}",
        color=GREEN if selection else AMBER,
    )
    if selection:
        _add(
            embed,
            "✅ Con ventaja validada",
            "\n".join(f"{esc(k)} → «{esc(v)}»" for k, v in sorted(selection.items())),
        )
    else:
        _add(
            embed,
            "Ninguna combinacion supero la validacion",
            "Despues de costes no se demostro ventaja en ningun instrumento, temporalidad ni plan. En modo estricto "
            "el bot no emite señales; `/analisis` sigue enseñando todo lo que ve.",
        )
    for plan, info in validation.get("plans", {}).items():
        rows = []
        for key, g in sorted(info.get("groups", {}).items()):
            summary = g.get("summary", {})
            if not summary or not summary.get("n"):
                continue
            mean_r = summary.get("mean_r")
            rows.append(
                f"{esc(key)}: {summary['n']} · TP1 {pct(summary.get('tp1_rate'))} · "
                f"{'n/d' if mean_r is None else f'{mean_r:+.3f}R'}{' ✅' if g.get('eligible') else ''}"
            )
        if rows:
            _add(embed, f"{plan} · umbral {pct(info.get('threshold_tp1'))}", "\n".join(rows))
    embed.set_footer(text="Informe completo: docs/validacion_2026-09-22.md")
    return embed


def status_embed(health: dict, cfg: AppConfig) -> discord.Embed:
    ks_active, ks_reason = health["kill_switch"]
    model = health.get("model")
    embed = discord.Embed(
        title="🩺 Estado",
        description=f"{esc(health['feed'])} · ultimo escaneo {clock(health['last_scan'])} · "
        f"modo {'estricto' if health['mode'] == 'strict' else 'informativo'}",
        color=RED if ks_active or health.get("last_error") else GREEN,
        timestamp=datetime.now(UTC),
    )
    _add(embed, "Señales abiertas", str(health["open_signals"]), True)
    _add(embed, "Interruptor", "🔴 ACTIVO" if ks_active else "🟢 inactivo", True)
    _add(embed, "Calendario", "OK" if health["calendar_ok"] else "⚠️ no disponible", True)
    _add(embed, "Modelo", f"entrenado {esc(str(model['trained_at'])[:10])}" if model else "⚠️ sin entrenar")
    _add(
        embed,
        "Datos",
        "\n".join(f"{symbol}: hasta {clock(d['complete_until'])}" for symbol, d in health["data"].items()),
    )
    if ks_active:
        _add(embed, "🔴 Motivo del interruptor", esc(ks_reason))
    if health.get("last_error"):
        _add(embed, "⚠️ Ultimo error", esc(health["last_error"]))
    return embed


def calendar_embed(events: list, cfg: AppConfig, failed: bool = False) -> discord.Embed:
    if failed:
        return discord.Embed(
            title="📅 Calendario no disponible",
            description="El feed gratuito no responde. No invento que no haya noticias: intentalo en unos minutos.",
            color=AMBER,
        )
    if not events:
        return discord.Embed(
            title="📅 Sin noticias de alto impacto",
            description="Nada relevante de USD/EUR en los proximos dias.",
            color=GREY,
        )
    lines = [f"`{when(e.time)}` {esc(e.currency)} — {esc(e.title)}" for e in events[:15]]
    return discord.Embed(title="📅 Alto impacto USD/EUR", description="\n".join(lines)[:4000], color=BLUE)


def help_embed(cfg: AppConfig, validated: int, model_ready: bool) -> discord.Embed:
    embed = discord.Embed(
        title="🤖 Como funciona",
        description=f"Vigila **{' y '.join(cfg.instruments)}** en M15, H1, H4 y D1 sobre velas cerradas. "
        "20 estrategias votan, 8 buscan la entrada y un modelo calibrado fuera de muestra estima la "
        "probabilidad real de cada objetivo.",
        color=BLUE,
    )
    _add(
        embed,
        "Te escribe solo",
        "Señal nueva (con zona de entrada, SL y TP1/TP2/TP3), aviso al tocar TP1, al cerrarse, y **si ve motivo "
        "para cerrar antes**: giro del contexto, entrada contraria, noticia encima, se acaba el tiempo o devuelves "
        "beneficio.",
    )
    _add(
        embed,
        "Comandos",
        "`/analisis` `/senales` `/stats` `/validacion` `/riesgo` `/calendario`\n"
        "`/capital` `/riesgo_pct` `/modo` `/silenciar` `/estado` `/reactivar` `/panel`",
    )
    if not model_ready:
        _add(embed, "⚠️ Sin modelo", "Ejecuta `python -m finance_bot research` en el PC del bot.")
    elif validated == 0:
        _add(
            embed,
            "⚠️ Importante",
            "Ninguna combinacion supero la validacion: despues de costes no se encontro ventaja. En modo estricto "
            "**no recibiras señales de entrada**. `/validacion` tiene los numeros; `/modo informativo` las enseña "
            "igualmente, marcadas como no validadas.",
        )
    return _disclaimer(embed, cfg)


def simple_embed(title: str, description: str, color: int = BLUE) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color)
