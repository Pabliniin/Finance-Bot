from __future__ import annotations

from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from backtest.metrics import compute_metrics
from bot.formatting import stats_embed
from bot.main import FinanzasBot
from config.loader import load_config
from tracking.ledger import read_equity_curve, read_trades

_PERIODS = {"7d": 7, "30d": 30, "90d": 90, "all": 3650}


class StatsCog(commands.Cog):
    def __init__(self, bot: FinanzasBot):
        self.bot = bot

    @app_commands.command(name="stats", description="Rendimiento REAL del bot en paper trading, sin maquillaje")
    @app_commands.describe(periodo="Ventana a mostrar")
    @app_commands.choices(periodo=[app_commands.Choice(name=p, value=p) for p in _PERIODS])
    async def stats(self, interaction: discord.Interaction, periodo: str = "30d") -> None:
        await interaction.response.defer()

        end = datetime.now(UTC)
        start = end - timedelta(days=_PERIODS.get(periodo, 30))

        trades_df = read_trades(self.bot.engine, "paper", start, end)
        equity_curve = read_equity_curve(self.bot.engine, "paper", start, end)

        if trades_df.empty:
            await interaction.followup.send(
                f"Sin operaciones cerradas en paper trading en el periodo '{periodo}'. "
                "Nada que reportar todavia — eso no es un error, es lo esperado al principio."
            )
            return

        capital_ref = load_config().account.capital_reference_eur
        trades_df["pnl_pct_of_capital"] = trades_df["pnl_eur"] / capital_ref

        metrics = compute_metrics(trades_df, equity_curve, capital_ref, start, end)
        await interaction.followup.send(embed=stats_embed(metrics, periodo))


async def setup(bot: FinanzasBot) -> None:
    await bot.add_cog(StatsCog(bot))
