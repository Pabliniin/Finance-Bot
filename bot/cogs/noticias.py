from __future__ import annotations

from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from bot.autocomplete import instrument_autocomplete
from bot.main import FinanzasBot
from config.loader import load_config
from data.storage.cache import read_news_headlines
from features.news_features import CURRENCY_KEYWORDS, add_sentiment_scores, instrument_currencies


class NoticiasCog(commands.Cog):
    def __init__(self, bot: FinanzasBot):
        self.bot = bot

    @app_commands.command(name="noticias", description="Titulares recientes con sentimiento y fuente")
    @app_commands.describe(ticker="Opcional: filtrar por instrumento, p.ej. EURUSD")
    @app_commands.autocomplete(ticker=instrument_autocomplete)
    async def noticias(self, interaction: discord.Interaction, ticker: str | None = None) -> None:
        await interaction.response.defer()
        end = datetime.now(UTC)
        start = end - timedelta(hours=48)

        headlines = read_news_headlines(self.bot.engine, start, end)
        if headlines.empty:
            await interaction.followup.send("No hay titulares en las ultimas 48h en cache.")
            return

        if ticker:
            ticker = ticker.upper()
            base, quote = instrument_currencies(ticker)
            currencies = {base, quote}

            def _mentions(row) -> bool:
                text = f"{row.get('headline', '')} {row.get('summary', '')}".lower()
                return any(kw in text for c in currencies for kw in CURRENCY_KEYWORDS.get(c, [c.lower()]))

            headlines = headlines[headlines.apply(_mentions, axis=1)]

        if headlines.empty:
            await interaction.followup.send(f"No hay titulares relevantes para {ticker} en las ultimas 48h.")
            return

        scored = add_sentiment_scores(headlines).tail(10)
        embed = discord.Embed(
            title=f"Noticias{' — ' + ticker if ticker else ''}", color=discord.Color.blurple()
        )
        for row in scored.itertuples():
            embed.add_field(
                name=f"[{row.sentiment_score:+.2f}] {row.source}",
                value=row.headline[:200], inline=False,
            )
        embed.set_footer(text=load_config().reporting.discord_footer)
        await interaction.followup.send(embed=embed)


async def setup(bot: FinanzasBot) -> None:
    await bot.add_cog(NoticiasCog(bot))
