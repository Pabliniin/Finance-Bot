from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from backtest.report_io import load_backtest_report
from bot.formatting import backtest_summary_embed
from bot.main import FinanzasBot
from strategy.registry import STRATEGY_CLASSES


class BacktestCog(commands.Cog):
    def __init__(self, bot: FinanzasBot):
        self.bot = bot

    @app_commands.command(name="backtest", description="Resumen de resultados de backtest fuera de muestra")
    @app_commands.choices(
        estrategia=[app_commands.Choice(name=name, value=name) for name in STRATEGY_CLASSES]
    )
    async def backtest(self, interaction: discord.Interaction, estrategia: str) -> None:
        await interaction.response.defer()
        report = load_backtest_report()
        await interaction.followup.send(embed=backtest_summary_embed(report, estrategia))


async def setup(bot: FinanzasBot) -> None:
    await bot.add_cog(BacktestCog(bot))
