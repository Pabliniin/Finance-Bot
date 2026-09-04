"""Autocompletado de instrumentos para los slash commands que piden un
ticker. Mejora de interfaz explicita: evita errores de tipeo y deja
descubrir el universo disponible (config/instruments.yaml) sin salir de
Discord, en vez de que el usuario tenga que adivinar el simbolo exacto.
"""

from __future__ import annotations

import discord
from discord import app_commands

from config.loader import load_instruments


async def instrument_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    current = current.upper()
    symbols = [i.symbol for i in load_instruments()]
    matches = [s for s in symbols if current in s] if current else symbols
    return [app_commands.Choice(name=s, value=s) for s in matches[:25]]
