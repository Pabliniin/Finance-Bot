from __future__ import annotations

from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from bot.autocomplete import instrument_autocomplete
from bot.main import FinanzasBot
from config.loader import load_config
from data.storage.cache import read_ohlcv
from risk.currency_conversion import CurrencyConversionError, resolve_quote_to_eur_rate
from risk.position_sizing import PositionSizingError, compute_position_size
from tracking.ledger import realized_pnl_eur


class RiesgoCog(commands.Cog):
    def __init__(self, bot: FinanzasBot):
        self.bot = bot

    @app_commands.command(name="riesgo", description="Calculadora de tamaño de posicion por riesgo fijo")
    @app_commands.describe(ticker="Instrumento, p.ej. EURUSD", precio_stop="Precio del stop loss")
    @app_commands.autocomplete(ticker=instrument_autocomplete)
    async def riesgo(self, interaction: discord.Interaction, ticker: str, precio_stop: float) -> None:
        await interaction.response.defer()
        ticker = ticker.upper()
        cfg = load_config()
        now = datetime.now(UTC)

        bars = read_ohlcv(self.bot.engine, ticker, "H1", now - timedelta(days=2), now, source=cfg.data.provider_live)
        if bars.empty:
            await interaction.followup.send(f"No hay precio reciente en cache para {ticker}.")
            return
        entry_price = float(bars.iloc[-1]["close"])

        capital = cfg.account.capital_reference_eur + realized_pnl_eur(
            self.bot.engine, "paper", datetime(2020, 1, 1, tzinfo=UTC), now
        )

        try:
            rate = resolve_quote_to_eur_rate(self.bot.engine, ticker, now, source=cfg.data.provider_live)
            size = compute_position_size(
                capital_eur=capital, risk_pct=cfg.account.max_risk_per_trade_pct,
                entry_price=entry_price, stop_loss_price=precio_stop, quote_to_eur_rate=rate,
            )
        except (PositionSizingError, CurrencyConversionError) as exc:
            await interaction.followup.send(f"No se puede calcular: {exc}")
            return

        embed = discord.Embed(title=f"Tamaño de posicion — {ticker}", color=discord.Color.blurple())
        embed.add_field(name="Capital actual (paper)", value=f"{capital:.2f} EUR", inline=True)
        embed.add_field(name="Riesgo maximo", value=f"{cfg.account.max_risk_per_trade_pct:.1f}%", inline=True)
        embed.add_field(name="Precio actual", value=f"{entry_price:.5f}", inline=True)
        embed.add_field(name="Stop propuesto", value=f"{precio_stop:.5f}", inline=True)
        embed.add_field(name="Distancia al stop", value=f"{size.stop_distance_price:.5f}", inline=True)
        embed.add_field(name="Lotes", value=f"{size.lots:.2f}", inline=True)
        embed.add_field(name="Riesgo real en EUR", value=f"{size.risk_eur:.2f} EUR", inline=True)
        embed.set_footer(text=cfg.reporting.discord_footer)
        await interaction.followup.send(embed=embed)


async def setup(bot: FinanzasBot) -> None:
    await bot.add_cog(RiesgoCog(bot))
