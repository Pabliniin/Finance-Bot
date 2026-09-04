from __future__ import annotations

from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from bot.main import FinanzasBot
from config.loader import load_config, load_instruments
from data.storage.cache import get_latest_ts, get_risk_state, read_recent_ingestion_log
from risk.kill_switch import get_status


class EstadoCog(commands.Cog):
    def __init__(self, bot: FinanzasBot):
        self.bot = bot

    @app_commands.command(name="estado", description="Salud del sistema: ultima actualizacion, errores, APIs")
    async def estado(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        cfg = load_config()
        now = datetime.now(UTC)

        kill_switch = get_status(self.bot.engine)
        last_run = get_risk_state(self.bot.engine, "last_daily_job")

        log_24h = read_recent_ingestion_log(self.bot.engine, now - timedelta(hours=24))
        failed = log_24h[log_24h["status"] == "failed"] if not log_24h.empty else log_24h
        degraded = log_24h[log_24h["status"] == "degraded"] if not log_24h.empty else log_24h

        instruments = load_instruments()
        stale_count = _count_stale_instruments(self.bot.engine, instruments, cfg.data.provider_live, now)

        kill_switch_text = (
            "🔴 SUSPENDIDO — " + (kill_switch.reason or "") if kill_switch.suspended else "🟢 activo"
        )
        last_run_text = str(last_run.get("ts")) if last_run else "nunca ejecutado"

        embed = discord.Embed(title="Estado del sistema", color=discord.Color.blurple())
        embed.add_field(name="Kill switch", value=kill_switch_text, inline=False)
        embed.add_field(name="Ultimo job diario", value=last_run_text, inline=True)
        embed.add_field(name="Ingestas fallidas (24h)", value=str(len(failed)), inline=True)
        embed.add_field(name="Ingestas degradadas (24h)", value=str(len(degraded)), inline=True)
        embed.add_field(
            name="Instrumentos con datos obsoletos (>26h)",
            value=f"{stale_count} / {len(instruments)}", inline=True,
        )
        await interaction.followup.send(embed=embed)


def _count_stale_instruments(engine, instruments, source: str, now: datetime, max_age_hours: int = 26) -> int:
    stale = 0
    for inst in instruments:
        latest = get_latest_ts(engine, inst.symbol, "H1", source)
        if latest is None or (now - latest) > timedelta(hours=max_age_hours):
            stale += 1
    return stale


async def setup(bot: FinanzasBot) -> None:
    await bot.add_cog(EstadoCog(bot))
