"""Entry point del bot de Discord (contenedor `bot` de docker-compose). Solo
sirve slash commands interactivos; el informe diario y las alertas los envia
scheduler/ via bot/notifier.py (login sin gateway), asi que este proceso no
necesita saber nada de la logica de negocio, solo consultar lo que ya esta en
Postgres.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.discord_log_handler import install as install_discord_alert_handler
from config.settings import get_settings
from data.storage.db import get_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log", encoding="utf-8")],
)
logger = logging.getLogger("bot.main")

_COGS = [
    "bot.cogs.senales",
    "bot.cogs.stats",
    "bot.cogs.analisis",
    "bot.cogs.riesgo",
    "bot.cogs.backtest_cmd",
    "bot.cogs.noticias",
    "bot.cogs.estado",
]


class FinanzasCommandTree(app_commands.CommandTree):
    """Subclase en vez de reasignar `tree.on_error` en la instancia: es el
    patron que documenta discord.py para un manejador global de errores, y
    deja claro el tipo real que usan los cogs (mypy no puede verificar una
    reasignacion de metodo con seguridad)."""

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Cualquier excepcion no capturada dentro de un cog cae aqui, para
        que el usuario nunca vea el mensaje generico de Discord ("La
        aplicacion no respondio") sin explicacion. El traceback completo va
        al log (y, via DiscordAlertHandler, al canal de alertas) - lo que ve
        el usuario es un mensaje corto y honesto."""
        command_name = interaction.command.name if interaction.command else "desconocido"
        logger.error("Error en /%s: %s", command_name, error, exc_info=error)

        message = "⚠️ Ha ocurrido un error inesperado ejecutando este comando. Se ha registrado para revision."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass  # si ni siquiera se puede notificar al usuario, ya queda constancia en el log


class FinanzasBot(commands.Bot):
    def __init__(self, database_url: str, guild_id: str | None):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents, tree_cls=FinanzasCommandTree)
        self.engine = get_engine(database_url)
        self._guild_id = int(guild_id) if guild_id else None

    async def setup_hook(self) -> None:
        for cog in _COGS:
            await self.load_extension(cog)
            logger.info("cog cargado: %s", cog)

        if self._guild_id:
            guild = discord.Object(id=self._guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("slash commands sincronizados en guild %s", self._guild_id)
        else:
            await self.tree.sync()
            logger.info("slash commands sincronizados globalmente (puede tardar hasta 1h en propagarse)")

    async def on_ready(self) -> None:
        logger.info("conectado como %s", self.user)


def main() -> None:
    settings = get_settings()  # se resuelve aqui, no al importar el modulo: un import no debe exigir .env valido
    if not settings.discord_bot_token:
        raise SystemExit("DISCORD_BOT_TOKEN no configurado en .env")

    install_discord_alert_handler()
    bot = FinanzasBot(settings.database_url, settings.discord_guild_id or None)
    bot.run(settings.discord_bot_token, log_handler=None)


if __name__ == "__main__":
    main()
