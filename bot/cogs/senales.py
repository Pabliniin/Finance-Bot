from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.formatting import open_positions_embed
from bot.main import FinanzasBot
from tracking.ledger import read_open_trades_with_signal


class SenalesCog(commands.Cog):
    def __init__(self, bot: FinanzasBot):
        self.bot = bot

    @app_commands.command(name="senales", description="Señales activas: entrada, stop, objetivo y razon")
    async def senales(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        positions = read_open_trades_with_signal(self.bot.engine, "paper")
        await interaction.followup.send(embed=open_positions_embed(positions))


async def setup(bot: FinanzasBot) -> None:
    await bot.add_cog(SenalesCog(bot))
