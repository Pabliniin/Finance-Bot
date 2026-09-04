from __future__ import annotations

from datetime import UTC, datetime, timedelta

import discord
import pandas as pd
from discord import app_commands
from discord.ext import commands

from bot.autocomplete import instrument_autocomplete
from bot.main import FinanzasBot
from config.loader import load_config
from data.providers.base import Timeframe
from data.storage.cache import read_news_headlines
from features.news_features import CURRENCY_KEYWORDS, add_sentiment_scores, instrument_currencies
from features.pipeline import build_feature_matrix


class AnalisisCog(commands.Cog):
    def __init__(self, bot: FinanzasBot):
        self.bot = bot

    @app_commands.command(name="analisis", description="Ficha de un instrumento: tecnico, volatilidad, noticias")
    @app_commands.describe(ticker="Instrumento, p.ej. EURUSD")
    @app_commands.autocomplete(ticker=instrument_autocomplete)
    async def analisis(self, interaction: discord.Interaction, ticker: str) -> None:
        await interaction.response.defer()
        ticker = ticker.upper()

        cfg = load_config()
        end = datetime.now(UTC)
        start = end - timedelta(days=30)

        matrix = build_feature_matrix(
            self.bot.engine, ticker, Timeframe.H1, start, end, source=cfg.data.provider_live,
        )
        if matrix.empty:
            await interaction.followup.send(
                f"No hay datos en cache para {ticker}. ¿Esta en config/instruments.yaml y ya ha corrido "
                "el collector de MT5 al menos una vez?"
            )
            return

        usable = matrix.dropna(subset=["ema_fast", "ema_slow", "rsi", "adx", "atr"])
        if usable.empty:
            await interaction.followup.send(
                f"Hay datos de {ticker} en cache pero no los suficientes todavia para calcular "
                "los indicadores (necesitan historico de calentamiento). Prueba de nuevo en unas horas."
            )
            return
        row = usable.iloc[-1]

        trend = "alcista" if row.ema_fast > row.ema_slow else "bajista"
        embed = discord.Embed(
            title=f"Analisis — {ticker}", description=f"Cierre: {row.close:.5f} · Tendencia: {trend}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="RSI", value=f"{row.rsi:.1f}", inline=True)
        embed.add_field(name="ADX", value=f"{row.adx:.1f}", inline=True)
        embed.add_field(name="ATR", value=f"{row.atr:.5f}", inline=True)
        embed.add_field(name="Regimen de volatilidad", value=str(row.get("vol_regime", "n/d")), inline=True)
        embed.add_field(
            name="Sentimiento de noticias (24h)",
            value=f"{row.get('news_sentiment_mean', 0):+.2f} ({int(row.get('news_mention_volume', 0))} titulares)",
            inline=True,
        )
        hours_to = row.get("hours_to_next_high_impact_event")
        embed.add_field(
            name="Proximo evento de alto impacto",
            value=f"en {hours_to:.1f}h" if pd.notna(hours_to) else "ninguno en el horizonte cargado",
            inline=True,
        )

        headlines = _recent_relevant_headlines(self.bot.engine, ticker, end)
        if not headlines.empty:
            lines = [
                f"[{h.sentiment_score:+.2f}] {h.headline} ({h.source})"
                for h in add_sentiment_scores(headlines).tail(5).itertuples()
            ]
            embed.add_field(name="Titulares recientes", value="\n".join(lines)[:1024], inline=False)

        embed.set_footer(text=cfg.reporting.discord_footer)
        await interaction.followup.send(embed=embed)


def _recent_relevant_headlines(engine, ticker: str, end: datetime):
    base, quote = instrument_currencies(ticker)
    currencies = {base, quote}
    start = end - timedelta(hours=48)
    headlines = read_news_headlines(engine, start, end)
    if headlines.empty:
        return headlines

    def _mentions(row) -> bool:
        text = f"{row.get('headline', '')} {row.get('summary', '')}".lower()
        return any(
            kw in text for c in currencies for kw in CURRENCY_KEYWORDS.get(c, [c.lower()])
        )

    return headlines[headlines.apply(_mentions, axis=1)]


async def setup(bot: FinanzasBot) -> None:
    await bot.add_cog(AnalisisCog(bot))
