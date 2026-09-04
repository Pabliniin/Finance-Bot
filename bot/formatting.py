"""Construccion de embeds de Discord. Todo mensaje que contenga una señal o
metrica de rendimiento lleva el pie de descargo fijo de config.yaml
(reporting.discord_footer) - es la unica funcion de este fichero que se
aplica sin excepcion, a proposito.
"""

from __future__ import annotations

from datetime import datetime

import discord
import pandas as pd

from backtest.metrics import BacktestMetrics
from config.loader import load_config
from risk.kill_switch import KillSwitchStatus
from tracking.model_monitor import DivergenceReport


def _with_footer(embed: discord.Embed) -> discord.Embed:
    embed.set_footer(text=load_config().reporting.discord_footer)
    return embed


def signal_embed(signal: dict) -> discord.Embed:
    color = discord.Color.green() if signal["direction"] == "long" else discord.Color.red()
    ts = signal["ts"] if isinstance(signal["ts"], datetime) else pd.Timestamp(signal["ts"]).to_pydatetime()
    embed = discord.Embed(
        title=f"{signal['instrument']} · {signal['direction'].upper()} · {signal['strategy']}",
        description=signal["reason"], color=color, timestamp=ts,
    )
    embed.add_field(name="Entrada", value=f"{signal['entry_price']:.5f}")
    embed.add_field(name="Stop", value=f"{signal['stop_loss']:.5f}")
    embed.add_field(name="Objetivo", value=f"{signal['take_profit']:.5f}")
    embed.add_field(name="Confianza", value=f"{signal['confidence']:.0%}")
    return _with_footer(embed)


def open_positions_embed(positions: pd.DataFrame) -> discord.Embed:
    if positions.empty:
        embed = discord.Embed(title="Señales activas", description="No hay posiciones abiertas en paper trading.")
        return _with_footer(embed)

    embed = discord.Embed(title="Señales activas", color=discord.Color.blurple())
    for _, pos in positions.iterrows():
        embed.add_field(
            name=f"{pos['instrument']} · {pos['direction'].upper()} · {pos['strategy']}",
            value=(
                f"Entrada: {pos['entry_fill']:.5f} | Stop: {pos['stop_loss']:.5f} | "
                f"Objetivo: {pos['take_profit']:.5f} | Lotes: {pos['lots']:.2f}"
            ),
            inline=False,
        )
    return _with_footer(embed)


def daily_report_embed(report: dict) -> discord.Embed:
    embed = discord.Embed(title=f"Informe diario — {report['date']}", color=discord.Color.blurple())
    embed.add_field(name="Señales generadas", value=str(report["signals_generated"]), inline=True)
    embed.add_field(name="Aprobadas", value=str(report["signals_approved"]), inline=True)
    embed.add_field(name="Rechazadas", value=str(report["signals_rejected"]), inline=True)
    embed.add_field(name="Posiciones abiertas", value=str(report["open_positions"]), inline=True)
    embed.add_field(name="Capital (paper)", value=f"{report['capital_eur']:.2f} EUR", inline=True)
    embed.add_field(
        name="Kill switch", value="🔴 SUSPENDIDO" if report["kill_switch_suspended"] else "🟢 activo", inline=True
    )
    if report.get("rejections"):
        text = "\n".join(f"- {r}" for r in report["rejections"][:10])
        embed.add_field(name="Motivos de rechazo", value=text[:1024], inline=False)
    if report.get("errors"):
        text = "\n".join(f"- {e}" for e in report["errors"][:10])
        embed.add_field(name="Errores/degradacion", value=text[:1024], inline=False)
    return _with_footer(embed)


def stats_embed(
    metrics: BacktestMetrics, period_label: str, divergence: DivergenceReport | None = None
) -> discord.Embed:
    embed = discord.Embed(title=f"Rendimiento real — {period_label}", color=discord.Color.blurple())
    embed.add_field(name="Operaciones", value=str(metrics.n_trades), inline=True)
    embed.add_field(name="Win rate", value=f"{metrics.win_rate:.1%}", inline=True)
    embed.add_field(name="Expectativa", value=f"{metrics.expectancy_eur:+.2f} EUR/op", inline=True)
    embed.add_field(name="Profit factor", value=f"{metrics.profit_factor:.2f}", inline=True)
    embed.add_field(name="CAGR", value=f"{metrics.cagr:+.1%}", inline=True)
    embed.add_field(name="Sharpe", value=f"{metrics.sharpe:.2f}", inline=True)
    embed.add_field(name="Sortino", value=f"{metrics.sortino:.2f}", inline=True)
    embed.add_field(
        name="Drawdown max",
        value=f"{metrics.max_drawdown_pct:.1%} ({metrics.max_drawdown_duration_days}d)", inline=True,
    )
    if divergence is not None:
        embed.add_field(name="Divergencia vs. backtest", value=divergence.message, inline=False)
    return _with_footer(embed)


def kill_switch_alert_embed(status: KillSwitchStatus) -> discord.Embed:
    embed = discord.Embed(
        title="🔴 Kill switch activado", description=status.reason or "motivo no especificado",
        color=discord.Color.red(),
    )
    embed.add_field(name="Desde", value=status.since or "desconocido")
    embed.add_field(
        name="Que significa esto",
        value="El bot ha dejado de emitir nuevas señales. Requiere revision manual antes de reactivarse.",
        inline=False,
    )
    return _with_footer(embed)


def backtest_summary_embed(report: dict | None, strategy: str) -> discord.Embed:
    if report is None or strategy not in report.get("strategies", {}):
        return _with_footer(discord.Embed(
            title=f"Backtest — {strategy}",
            description="Aun no hay resultados de backtest guardados para esta estrategia.",
            color=discord.Color.greyple(),
        ))

    data = report["strategies"][strategy]
    embed = discord.Embed(
        title=f"Backtest — {strategy} (fuera de muestra)",
        description=f"Generado: {report.get('generated_at', 'desconocido')}",
        color=discord.Color.gold() if data.get("discarded") else discord.Color.blurple(),
    )
    if data.get("discarded"):
        embed.add_field(
            name="⚠️ DESCARTADA", value=data.get("discard_reason", "no supera el test vs. azar"), inline=False
        )
    for key in ("n_trades", "win_rate", "expectancy_eur", "sharpe", "max_drawdown_pct", "profit_factor"):
        if key in data:
            embed.add_field(name=key, value=str(data[key]), inline=True)
    return _with_footer(embed)
