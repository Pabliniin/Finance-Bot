"""Envio de mensajes "de una vez" (login HTTP sin conectar el gateway
completo) para que scheduler/ pueda publicar el informe diario y las alertas
sin mantener una conexion persistente a Discord - esa la mantiene bot/main.py
por separado, para los slash commands interactivos.
"""

from __future__ import annotations

import logging

import discord

from config.settings import get_settings

logger = logging.getLogger(__name__)


async def send_embed(channel_id: str, embed: discord.Embed) -> None:
    settings = get_settings()
    if not settings.discord_bot_token or not channel_id:
        logger.warning("Discord no configurado (token/canal vacio); no se envia el embed '%s'", embed.title)
        return

    client = discord.Client(intents=discord.Intents.default())
    try:
        await client.login(settings.discord_bot_token)
        channel = client.get_partial_messageable(int(channel_id))
        await channel.send(embed=embed)
    finally:
        await client.close()


async def send_embeds(channel_id: str, embeds: list[discord.Embed]) -> None:
    """Version por lotes de send_embed: abre UN solo cliente para varios
    mensajes en vez de uno por señal, para no pagar el coste de login/close
    de discord.py N veces en el job diario."""
    if not embeds:
        return
    settings = get_settings()
    if not settings.discord_bot_token or not channel_id:
        logger.warning("Discord no configurado; no se envian %d embeds", len(embeds))
        return

    client = discord.Client(intents=discord.Intents.default())
    try:
        await client.login(settings.discord_bot_token)
        channel = client.get_partial_messageable(int(channel_id))
        for embed in embeds:
            await channel.send(embed=embed)
    finally:
        await client.close()


async def send_text(channel_id: str, content: str) -> None:
    settings = get_settings()
    if not settings.discord_bot_token or not channel_id:
        logger.warning("Discord no configurado (token/canal vacio); no se envia el mensaje")
        return

    client = discord.Client(intents=discord.Intents.default())
    try:
        await client.login(settings.discord_bot_token)
        channel = client.get_partial_messageable(int(channel_id))
        await channel.send(content)
    finally:
        await client.close()
