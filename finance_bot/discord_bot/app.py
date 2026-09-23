"""Bot de Discord (discord.py 2.x, comandos de barra).

Seguridad:
- Solo funciona en el servidor autorizado (DISCORD_GUILD_ID, o el servidor en el
  que esta al arrancar). En cualquier otro no responde.
- Los ajustes (capital, riesgo, modo, silenciar, reactivar) solo los cambian los
  usuarios de DISCORD_ADMIN_USER_IDS si esa lista existe.
- El token nunca se escribe en los logs.
- El bot JAMAS opera: no tiene acceso de trading a ninguna cuenta.

Rendimiento: todo lo pesado (datos, analisis, MT5) va a UN hilo dedicado, porque
el paquete MetaTrader5 no es seguro entre hilos. Asi el bot nunca se congela.

Los botones se manejan por custom_id en on_interaction, no con vistas en memoria:
siguen funcionando despues de reiniciar el bot.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from typing import Any

import discord
from discord import app_commands
from discord.ext import tasks

from finance_bot import updater
from finance_bot.discord_bot import embeds
from finance_bot.engine.exits import r_now
from finance_bot.logging_setup import setup_logging
from finance_bot.service import BotService

logger = logging.getLogger(__name__)

EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="finance-bot-worker")
CHANNEL_PREFERENCE = ("senales", "señales", "signals", "trading", "alertas", "bot", "general")
PANEL_SETTING = "discord_panel_message_id"
CHANNEL_SETTING = "discord_channel_id"
# Si en este tiempo no se completa ningun ciclo, algo esta colgado y se reinicia el proceso.
WATCHDOG_STALL = timedelta(minutes=15)


def _button(label: str, custom_id: str, style: discord.ButtonStyle, emoji: str | None = None) -> discord.ui.Button:
    return discord.ui.Button(label=label, custom_id=custom_id, style=style, emoji=emoji)


def signal_view(symbol: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(_button("Analisis completo", f"fb:analisis:{symbol}", discord.ButtonStyle.primary, "📊"))
    view.add_item(_button("Silenciar 4 h", f"fb:mute:{symbol}:4", discord.ButtonStyle.secondary, "🔕"))
    return view


def panel_view(symbols: list[str]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for symbol in symbols[:3]:
        view.add_item(_button(symbol, f"fb:analisis:{symbol}", discord.ButtonStyle.primary, "📈"))
    view.add_item(_button("Abiertas", "fb:senales", discord.ButtonStyle.secondary, "📌"))
    view.add_item(_button("Resultados", "fb:stats:30d", discord.ButtonStyle.secondary, "📊"))
    return view


class FinanceBot(discord.Client):
    def __init__(self, service: BotService):
        # Sin intents privilegiados: asi no hay que activar nada en el portal.
        super().__init__(intents=discord.Intents.default())
        self.service = service
        self.tree = app_commands.CommandTree(self)
        self.allowed_guild_ids: set[int] = set()
        self.channel: discord.TextChannel | None = None
        self.last_data_error: str | None = None
        self._ready_once = False
        self._panel_lock = asyncio.Lock()
        self._presence_index = -1
        self.restart_requested = False
        self._last_cycle_done = datetime.now(UTC)

    # --- utilidades -------------------------------------------------------------

    async def run_blocking(self, fn: Callable[..., Any], *args: Any) -> Any:
        """MT5 y pandas fuera del bucle de eventos, en el hilo unico."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(EXECUTOR, functools.partial(fn, *args))

    def is_allowed(self, interaction: discord.Interaction) -> bool:
        return interaction.guild_id is not None and interaction.guild_id in self.allowed_guild_ids

    def is_admin(self, user_id: int) -> bool:
        admins = self.service.secrets.admin_user_ids
        return not admins or user_id in admins

    def mention_admins(self) -> str:
        return " ".join(f"<@{uid}>" for uid in sorted(self.service.secrets.admin_user_ids))

    def ping_content(self) -> str | None:
        """A quien se avisa de lo importante (config notifications.mention)."""
        modo = self.service.cfg.notifications.mention
        if modo == "here":
            return "@here"
        if modo == "admins":
            return self.mention_admins() or None
        return None

    async def send(self, embed: discord.Embed, view: discord.ui.View | None = None, ping: bool = False) -> None:
        if self.channel is None:
            logger.warning("Sin canal donde publicar: %s", embed.title)
            return
        content = self.ping_content() if ping else None
        try:
            await self.channel.send(
                content=content,
                embed=embed,
                view=view or discord.utils.MISSING,
                allowed_mentions=discord.AllowedMentions(everyone=True, users=True, roles=False),
            )
        except discord.HTTPException:
            logger.exception("no se pudo enviar el mensaje al canal")

    # --- arranque ---------------------------------------------------------------

    async def setup_hook(self) -> None:
        register_commands(self)

    async def on_ready(self) -> None:
        if self._ready_once:
            return
        self._ready_once = True
        configured = self.service.secrets.discord_guild_id
        guilds = [g for g in self.guilds if not configured or g.id == configured]
        self.allowed_guild_ids = {g.id for g in guilds}
        if not guilds:
            logger.error(
                "El bot no esta en ningun servidor%s. Invitalo con: python -m finance_bot invitar",
                f" con id {configured}" if configured else "",
            )
        if not configured and len(guilds) == 1:
            logger.info(
                "Atado al servidor '%s' (id %s). Ponlo en DISCORD_GUILD_ID del .env para fijarlo.",
                guilds[0].name,
                guilds[0].id,
            )
        for guild in guilds:
            await self._sync_commands(guild)
        await self._resolve_channel(guilds)
        self._start_jobs()
        if guilds:
            await self._announce()
        logger.info("Bot listo como %s en %d servidor(es)", self.user, len(guilds))
        updater.boot_guard_ok()
        asyncio.create_task(self.self_update())  # por si hubo arreglos mientras estaba apagado

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Si lo invitas con el bot ya arrancado, se configura al momento."""
        configured = self.service.secrets.discord_guild_id
        if configured and guild.id != configured:
            logger.warning("Invitado a '%s' (%s), que no es el servidor configurado: lo ignoro.", guild.name, guild.id)
            return
        logger.info("Invitado al servidor '%s' (%s)", guild.name, guild.id)
        self.allowed_guild_ids.add(guild.id)
        await self._sync_commands(guild)
        if self.channel is None or self.channel.guild.id != guild.id:
            await self._resolve_channel([guild])
        await self._announce()

    async def _sync_commands(self, guild: discord.Guild) -> None:
        obj = discord.Object(id=guild.id)
        self.tree.copy_global_to(guild=obj)
        await self.tree.sync(guild=obj)  # asi aparecen al instante en ese servidor

    async def _resolve_channel(self, guilds: list[discord.Guild]) -> None:
        stored = self.service.tracker.get_setting(CHANNEL_SETTING)
        wanted = self.service.secrets.discord_channel_id or (int(stored) if stored else 0)
        if wanted:
            channel = self.get_channel(wanted)
            if isinstance(channel, discord.TextChannel):
                self.channel = channel
                return
            logger.warning("El canal %s ya no existe o no lo veo; busco otro.", wanted)
        for guild in guilds:
            me = guild.me
            candidates = [
                c
                for c in guild.text_channels
                # sin miembro propio en cache no podemos comprobar permisos: se intenta igual
                if me is None or (c.permissions_for(me).send_messages and c.permissions_for(me).embed_links)
            ]
            if not candidates:
                continue
            candidates.sort(
                key=lambda c: next((i for i, n in enumerate(CHANNEL_PREFERENCE) if n in c.name.lower()), 99)
            )
            self.channel = candidates[0]
            self.service.tracker.set_setting(CHANNEL_SETTING, str(candidates[0].id))
            logger.info("Publicare en #%s (%s). Cambialo con DISCORD_CHANNEL_ID.", candidates[0].name, candidates[0].id)
            return
        logger.error("No encuentro ningun canal donde pueda escribir. Dame permiso de 'Enviar mensajes'.")

    async def _announce(self) -> None:
        """Arrancar no es noticia: el panel ya dice como esta todo. Solo se
        presenta la primera vez en un canal, o si falta el modelo."""
        service = self.service
        first_time = service.tracker.get_setting(PANEL_SETTING) is None
        if first_time or service.artifacts is None:
            validated = len(service.artifacts.validation.get("selection", {})) if service.artifacts else 0
            embed = embeds.help_embed(service.cfg, validated, service.artifacts is not None)
            await self.send(embed)
        await self.update_panel()

    def _start_jobs(self) -> None:
        schedule = self.service.cfg.schedule
        self.scan_loop.change_interval(minutes=schedule.scan_every_minutes)
        hh, mm = (int(x) for x in schedule.daily_report_utc.split(":"))
        self.daily_loop.change_interval(time=time(hh, mm, tzinfo=UTC))
        self.maintenance_loop.change_interval(time=time(22, 30, tzinfo=UTC))
        self._last_cycle_done = datetime.now(UTC)
        for loop in (self.scan_loop, self.daily_loop, self.maintenance_loop, self.update_loop, self.watchdog_loop):
            if not loop.is_running():
                loop.start()

    # --- panel ------------------------------------------------------------------

    async def r_by_key(self, open_signals: Any) -> dict[str, float]:
        out: dict[str, float] = {}
        if open_signals.empty or self.service.feed is None:
            return out
        for symbol in open_signals["symbol"].unique():
            try:
                quote = await self.run_blocking(self.service.feed.quote, symbol)
            except Exception:  # noqa: BLE001 - sin cotizacion no hay R en vivo, pero el panel sigue
                logger.warning("sin cotizacion de %s para el R en vivo", symbol)
                continue
            for _, s in open_signals[open_signals["symbol"] == symbol].iterrows():
                value = r_now(float(s["entry"]), float(s["r_price"]), int(s["direction"]), quote)
                if value is not None:
                    out[str(s["key"])] = value
        return out

    async def build_panel(self, open_signals: Any = None, r_by_key: dict[str, float] | None = None) -> discord.Embed:
        """Con los datos ya calculados por el escaneo no se repite el trabajo."""
        service = self.service
        if open_signals is None:
            open_signals = await self.run_blocking(service.tracker.open_signals)
        if r_by_key is None:
            r_by_key = await self.r_by_key(open_signals)
        health = await self.run_blocking(service.health)
        return embeds.panel_embed(service.latest_analyses, open_signals, health, service.cfg, r_by_key)

    async def update_panel(
        self, force_new: bool = False, open_signals: Any = None, r_by_key: dict[str, float] | None = None
    ) -> None:
        """Un unico mensaje fijo con el estado. Se edita, no se repite.

        El cerrojo evita el caso real de arrancar y escanear a la vez: sin el,
        las dos llamadas leen "no hay panel" y crean uno cada una."""
        async with self._panel_lock:
            await self._update_panel(force_new, open_signals, r_by_key)

    async def _update_panel(self, force_new: bool, open_signals: Any, r_by_key: dict[str, float] | None) -> None:
        if self.channel is None:
            return
        embed = await self.build_panel(open_signals, r_by_key)
        view = panel_view(list(self.service.cfg.instruments))
        stored = self.service.tracker.get_setting(PANEL_SETTING)
        if stored and not force_new:
            try:
                message = await self.channel.fetch_message(int(stored))
                await message.edit(embed=embed, view=view)
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.info("El panel anterior ya no esta; creo uno nuevo.")
        try:
            message = await self.channel.send(embed=embed, view=view)
            self.service.tracker.set_setting(PANEL_SETTING, str(message.id))
        except discord.HTTPException:
            logger.exception("no se pudo crear el panel")
            return
        try:
            await message.pin()  # que quede arriba del canal; si no hay permiso, da igual
        except discord.HTTPException:
            logger.info("Sin permiso para fijar el panel (no pasa nada).")

    # --- botones ----------------------------------------------------------------

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is not discord.InteractionType.component or not interaction.data:
            return
        custom_id = str(interaction.data.get("custom_id", ""))
        if not custom_id.startswith("fb:"):
            return
        if not self.is_allowed(interaction):
            await interaction.response.send_message("⛔ Este bot es privado.", ephemeral=True)
            return
        parts = custom_id.split(":")
        action = parts[1]
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            if action == "analisis":
                embed = await self.analysis_embed(parts[2])
                await interaction.followup.send(embed=embed, ephemeral=True)
            elif action == "mute":
                if not self.is_admin(interaction.user.id):
                    await interaction.followup.send("⛔ Solo los administradores del bot.", ephemeral=True)
                    return
                symbol, hours = parts[2], float(parts[3])
                until = datetime.now(UTC) + timedelta(hours=hours)
                self.service.tracker.set_setting(f"mute_{symbol}", until.isoformat())
                await interaction.followup.send(
                    f"🔕 {symbol} silenciado hasta {embeds.when(until)}. El seguimiento de las abiertas sigue.",
                    ephemeral=True,
                )
            elif action == "senales":
                open_signals = await self.run_blocking(self.service.tracker.open_signals)
                embed = embeds.open_signals_embed(open_signals, self.service.cfg, await self.r_by_key(open_signals))
                await interaction.followup.send(embed=embed, ephemeral=True)
            elif action == "stats":
                await interaction.followup.send(embed=await self.stats_embed(parts[2]), ephemeral=True)
        except Exception:  # noqa: BLE001 - un boton roto no debe tumbar el bot
            logger.exception("boton %s", custom_id)
            await interaction.followup.send("⚠️ Error interno. Queda en logs/bot.log.", ephemeral=True)

    # --- piezas reutilizadas por comandos y botones ------------------------------

    async def analysis_embed(self, symbol: str) -> discord.Embed:
        service = self.service
        if symbol not in service.cfg.instruments:
            return embeds.simple_embed(
                "Instrumento desconocido", f"Opciones: {', '.join(service.cfg.instruments)}", embeds.AMBER
            )
        # Si el escaneo automatico lleva un rato sin completarse (MT5 caido, arranque),
        # el analisis usaria velas viejas y diria "llega tarde" a todo: se refresca antes.
        stale = service.last_scan is None or datetime.now(UTC) - service.last_scan > timedelta(minutes=3)
        if not service.complete_until or stale:
            await self.run_blocking(service.refresh_with_fallback)
        analysis = await self.run_blocking(service.analyze, symbol)
        return embeds.analysis_embed(analysis, service.cfg)

    async def stats_embed(self, period: str) -> discord.Embed:
        days = {"24h": 1, "7d": 7, "30d": 30, "90d": 90, "todo": None}.get(period, 30)
        since = datetime.now(UTC) - timedelta(days=days) if days else None
        closed = await self.run_blocking(self.service.tracker.closed_signals, since)
        scoreboard = await self.run_blocking(self.service.tracker.advice_scoreboard)
        return embeds.stats_embed(closed, scoreboard, period, self.service.cfg)

    # --- estado del bot en Discord -----------------------------------------------

    def _presence_texts(self, open_signals: Any, r_by_key: dict[str, float]) -> list[str]:
        """Frases cortas que van rotando bajo el nombre del bot."""
        texts: list[str] = []
        for symbol, analysis in self.service.latest_analyses.items():
            digits = self.service.cfg.instrument(symbol).digits
            short = symbol.replace("USD", "") if symbol != "EURUSD" else "EUR"
            if analysis.quote:
                texts.append(f"{short} {embeds.price(analysis.quote[0], digits)}")
            main = analysis.views.get("H1") or next(iter(analysis.views.values()), None)
            if main is not None:
                texts.append(f"{short} {main.tf} {main.bias} ({main.score:+d})")
        if open_signals is not None and not open_signals.empty:
            total = sum(r_by_key.values()) if r_by_key else None
            resume = f"{len(open_signals)} abierta(s)"
            if total is not None and r_by_key:
                resume += f" · {total:+.1f}R"
            texts.append(resume)
        else:
            texts.append("sin señales abiertas")
        if self.service.last_scan:
            texts.append(f"ultimo vistazo {embeds.clock(self.service.last_scan)}")
        return texts or ["vigilando el mercado"]

    async def update_presence(self, open_signals: Any = None, r_by_key: dict[str, float] | None = None) -> None:
        texts = self._presence_texts(open_signals, r_by_key or {})
        self._presence_index = (self._presence_index + 1) % len(texts)
        try:
            await self.change_presence(activity=discord.CustomActivity(name=texts[self._presence_index][:128]))
        except discord.HTTPException:
            logger.debug("no se pudo actualizar el estado", exc_info=True)

    # --- tareas periodicas -------------------------------------------------------

    @tasks.loop(minutes=1)
    async def scan_loop(self) -> None:
        """Nada de lo que pase dentro puede matar el bucle: sin nadie mirando,
        un bucle muerto es un bot que parece vivo y no hace nada."""
        try:
            await self._scan_once()
        except Exception:  # noqa: BLE001
            logger.exception("ciclo de escaneo fallido; se reintenta en el siguiente")
        self._last_cycle_done = datetime.now(UTC)

    @tasks.loop(minutes=5)
    async def watchdog_loop(self) -> None:
        """Vigila al vigilante. Si el bucle murio, lo relanza. Si lleva demasiado
        sin terminar un ciclo (MT5 colgado bloqueando el hilo unico), el
        proceso se reinicia entero: la tarea programada lo vuelve a levantar."""
        if not self.scan_loop.is_running():
            logger.error("El bucle de escaneo estaba parado; lo relanzo")
            self.scan_loop.start()
            return
        silence = datetime.now(UTC) - self._last_cycle_done
        if silence > WATCHDOG_STALL:
            logger.critical("Sin completar un ciclo desde hace %s: reinicio el proceso", silence)
            await self.send(
                embeds.simple_embed("🔁 Reinicio", "Llevo demasiado sin completar un ciclo; me reinicio.", embeds.AMBER)
            )
            updater.schedule_restart()
            os._exit(updater.EXIT_RESTART)  # el hilo colgado impediria una salida limpia

    async def _scan_once(self) -> None:
        service = self.service
        try:
            result = await self.run_blocking(service.scan)
        except Exception:  # noqa: BLE001
            logger.exception("escaneo fallido")
            return
        for signal in result.new_signals:
            await self.send(embeds.signal_embed(signal, service.cfg), signal_view(signal.symbol), ping=True)
        # "avisame cuando cerrar": todo lo que pide actuar sobre una operacion abierta avisa
        for advice in result.advice:
            await self.send(embeds.advice_embed(advice, service.cfg), ping=True)
        for event in result.events:
            await self.send(embeds.event_embed(event, service.cfg), ping=True)
        if result.kill_switch_reason:
            await self.send(
                embeds.simple_embed(
                    "🔴 Interruptor de seguridad activado",
                    f"{result.kill_switch_reason}\n\nNo se emitiran señales nuevas hasta que lo revises y uses "
                    "`/reactivar`.",
                    embeds.RED,
                ),
                ping=True,
            )
        error = "; ".join(result.errors) or result.notice
        if error != self.last_data_error:
            self.last_data_error = error
            if error:
                await self.send(
                    embeds.simple_embed(
                        "⚠️ Problema con los datos",
                        f"{error}\n\nNo se emiten señales con datos dudosos.",
                        embeds.AMBER,
                    )
                )
            else:
                await self.send(embeds.simple_embed("✅ Datos recuperados", "El escaneo vuelve a la normalidad."))
        open_signals = await self.run_blocking(service.tracker.open_signals)
        r_by_key = await self.r_by_key(open_signals)
        await self.update_panel(open_signals=open_signals, r_by_key=r_by_key)
        await self.update_presence(open_signals, r_by_key)
        self._adjust_cadence()

    def _adjust_cadence(self) -> None:
        """Con MT5 se mira el mercado cada minuto. Con el respaldo gratuito de
        Dukascopy se espacia: sus datos llegan con retraso y no hay que
        castigar un servidor publico pidiendo lo mismo sesenta veces por hora."""
        realtime = bool(self.service.feed and self.service.feed.realtime)
        wanted = (
            self.service.cfg.schedule.scan_every_minutes
            if realtime
            else max(3, self.service.cfg.schedule.scan_every_minutes)
        )
        if self.scan_loop.minutes != wanted:
            logger.info(
                "Cadencia de escaneo: cada %d min (fuente %s)",
                wanted,
                self.service.feed.name if self.service.feed else "?",
            )
            self.scan_loop.change_interval(minutes=wanted)

    @tasks.loop(time=time(21, 30, tzinfo=UTC))
    async def daily_loop(self) -> None:
        try:
            await self._daily_report()
        except Exception:  # noqa: BLE001
            logger.exception("informe diario fallido")

    async def _daily_report(self) -> None:
        service = self.service
        embed = await self.stats_embed("24h")
        embed.title = "🌙 Informe diario"
        await self.send(embed)
        if datetime.now(UTC).weekday() == service.cfg.schedule.weekly_report_weekday:
            weekly = await self.stats_embed("todo")
            weekly.title = "🗓 Resumen semanal (todo el historico del bot)"
            await self.send(weekly)

    async def self_update(self) -> None:
        """Si hay codigo nuevo en GitHub, se aplica y el bot se reinicia solo
        (la tarea programada lo vuelve a levantar). Nadie tiene que tocar el
        PC donde vive."""
        try:
            applied = await self.run_blocking(updater.check_and_apply)
        except Exception:  # noqa: BLE001
            logger.exception("comprobacion de actualizaciones fallida")
            return
        if not applied:
            return
        logger.info("Codigo actualizado: reiniciando")
        await self.send(embeds.simple_embed("🔄 Actualizado", "Hay una version nueva; me reinicio en unos segundos."))
        self.restart_requested = True
        await self.close()

    @tasks.loop(hours=1)
    async def update_loop(self) -> None:
        """Una consulta barata a GitHub cada hora: asi un arreglo llega al PC
        del bot sin que nadie lo toque, y sin esperar a la noche."""
        try:
            await self.self_update()
        except Exception:  # noqa: BLE001
            logger.exception("comprobacion de actualizacion fallida")

    @tasks.loop(time=time(22, 30, tzinfo=UTC))
    async def maintenance_loop(self) -> None:
        try:
            summary = await self.run_blocking(self.service.maintenance)
            logger.info("Mantenimiento: %s", summary)
        except Exception:  # noqa: BLE001
            logger.exception("mantenimiento diario fallo")

    @scan_loop.before_loop
    @watchdog_loop.before_loop
    @update_loop.before_loop
    @daily_loop.before_loop
    @maintenance_loop.before_loop
    async def _wait_ready(self) -> None:
        await self.wait_until_ready()


def register_commands(bot: FinanceBot) -> None:  # noqa: C901 - un bloque por comando, mas legible junto
    cfg = bot.service.cfg
    tree = bot.tree
    symbol_choices = [app_commands.Choice(name=s, value=s) for s in cfg.instruments]

    async def guard(interaction: discord.Interaction, admin: bool = False) -> bool:
        if not bot.is_allowed(interaction):
            await interaction.response.send_message("⛔ Este bot es privado.", ephemeral=True)
            return False
        if admin and not bot.is_admin(interaction.user.id):
            await interaction.response.send_message(
                "⛔ Solo los administradores del bot pueden cambiar ajustes. Tu id es "
                f"`{interaction.user.id}` (va en DISCORD_ADMIN_USER_IDS del .env).",
                ephemeral=True,
            )
            return False
        return True

    @tree.command(name="analisis", description="Todo lo que ve el bot de un instrumento, ahora mismo")
    @app_commands.describe(instrumento="XAUUSD (oro) o EURUSD")
    @app_commands.choices(instrumento=symbol_choices)
    async def analisis(interaction: discord.Interaction, instrumento: app_commands.Choice[str]) -> None:
        if not await guard(interaction):
            return
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(embed=await bot.analysis_embed(instrumento.value))

    @tree.command(name="senales", description="Señales abiertas y como van")
    async def senales(interaction: discord.Interaction) -> None:
        if not await guard(interaction):
            return
        await interaction.response.defer(thinking=True)
        open_signals = await bot.run_blocking(bot.service.tracker.open_signals)
        await interaction.followup.send(
            embed=embeds.open_signals_embed(open_signals, cfg, await bot.r_by_key(open_signals))
        )

    @tree.command(name="seguir", description="Vigila una operacion TUYA y te avisa cuando convenga cerrarla")
    @app_commands.describe(
        instrumento="XAUUSD (oro) o EURUSD",
        direccion="Si has comprado o vendido",
        entrada="Precio al que entraste",
        stop="Tu stop loss",
        temporalidad="Con que ritmo vigilarla (por defecto H1)",
    )
    @app_commands.choices(
        instrumento=symbol_choices,
        direccion=[
            app_commands.Choice(name="compra", value="compra"),
            app_commands.Choice(name="venta", value="venta"),
        ],
        temporalidad=[app_commands.Choice(name=tf, value=tf) for tf in ("M15", "H1", "H4", "D1")],
    )
    async def seguir(
        interaction: discord.Interaction,
        instrumento: app_commands.Choice[str],
        direccion: app_commands.Choice[str],
        entrada: float,
        stop: float,
        temporalidad: app_commands.Choice[str] | None = None,
    ) -> None:
        if not await guard(interaction, admin=True):
            return
        tf = temporalidad.value if temporalidad else "H1"
        try:
            key = await bot.run_blocking(
                bot.service.tracker.follow_manual,
                instrumento.value,
                tf,
                1 if direccion.value == "compra" else -1,
                entrada,
                stop,
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        open_signals = await bot.run_blocking(bot.service.tracker.open_signals)
        row = open_signals[open_signals["key"] == key].iloc[0]
        digits = cfg.instrument(instrumento.value).digits
        await interaction.response.send_message(
            embed=embeds.simple_embed(
                f"👤 Vigilando tu {direccion.value} de {instrumento.value} {tf}",
                f"```\nEntrada {embeds.price(entrada, digits)}\nSL      {embeds.price(stop, digits)}\n"
                f"TP1     {embeds.price(row['tp1'], digits)}\nTP2     {embeds.price(row['tp2'], digits)}\n```"
                f"Te aviso al tocar TP1, al cerrarse y si veo motivo para salir antes.\n"
                f"No lleva probabilidad del modelo (no es un setup suyo) y no entra en `/stats`.\n"
                f"Para dejarlo: `/dejar`.",
                embeds.GREEN if direccion.value == "compra" else embeds.RED,
            )
        )

    @tree.command(name="dejar", description="Deja de vigilar tus operaciones manuales")
    @app_commands.describe(instrumento="Solo las de este instrumento (si no, todas)")
    @app_commands.choices(instrumento=symbol_choices)
    async def dejar(interaction: discord.Interaction, instrumento: app_commands.Choice[str] | None = None) -> None:
        if not await guard(interaction, admin=True):
            return
        open_signals = await bot.run_blocking(bot.service.tracker.open_signals)
        manual = (
            open_signals[open_signals["source"] == "manual"] if "source" in open_signals else open_signals.iloc[0:0]
        )
        if instrumento is not None:
            manual = manual[manual["symbol"] == instrumento.value]
        stopped = 0
        for key in manual["key"]:
            stopped += int(await bot.run_blocking(bot.service.tracker.stop_following, key))
        await interaction.response.send_message(
            f"✅ Dejo de vigilar {stopped} operacion(es)." if stopped else "No habia ninguna operacion tuya vigilada.",
            ephemeral=True,
        )

    @tree.command(name="stats", description="Resultados reales del seguimiento en papel")
    @app_commands.describe(periodo="Ventana de tiempo")
    @app_commands.choices(
        periodo=[
            app_commands.Choice(name="7 dias", value="7d"),
            app_commands.Choice(name="30 dias", value="30d"),
            app_commands.Choice(name="90 dias", value="90d"),
            app_commands.Choice(name="Todo", value="todo"),
        ]
    )
    async def stats(interaction: discord.Interaction, periodo: app_commands.Choice[str] | None = None) -> None:
        if not await guard(interaction):
            return
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(embed=await bot.stats_embed(periodo.value if periodo else "30d"))

    @tree.command(name="validacion", description="Que ha superado la validacion fuera de muestra y con que numeros")
    async def validacion(interaction: discord.Interaction) -> None:
        if not await guard(interaction):
            return
        validation = bot.service.artifacts.validation if bot.service.artifacts else None
        await interaction.response.send_message(embed=embeds.validation_embed(validation, cfg))

    @tree.command(name="riesgo", description="Cuantos lotes para tu riesgo, dado el precio del stop")
    @app_commands.describe(instrumento="XAUUSD o EURUSD", stop="Precio de tu stop", riesgo_pct="% de tu capital")
    @app_commands.choices(instrumento=symbol_choices)
    async def riesgo(
        interaction: discord.Interaction,
        instrumento: app_commands.Choice[str],
        stop: float,
        riesgo_pct: float | None = None,
    ) -> None:
        if not await guard(interaction):
            return
        from finance_bot.risk import position_size

        await interaction.response.defer(thinking=True)
        service = bot.service
        symbol = instrumento.value
        pct_value = riesgo_pct if riesgo_pct is not None else service.tracker.risk_pct()
        if not 0 < pct_value <= 100:
            await interaction.followup.send(embed=embeds.simple_embed("Riesgo no valido", "Usa un % entre 0 y 100."))
            return
        quote = await bot.run_blocking(service.feed.quote, symbol) if service.feed else None
        eur = await bot.run_blocking(service.feed.quote, "EURUSD") if service.feed else None
        if quote is None or eur is None:
            since = datetime.now(UTC) - timedelta(days=10)
            bars = await bot.run_blocking(service.md.base_h1, symbol, since)
            eur_bars = await bot.run_blocking(service.md.base_h1, "EURUSD", since)
            if bars.empty or eur_bars.empty:
                await interaction.followup.send(
                    embed=embeds.simple_embed("Sin precio", "No tengo precio reciente para calcular.", embeds.AMBER)
                )
                return
            last, eurusd = float(bars["close"].iloc[-1]), float(eur_bars["close"].iloc[-1])
        else:
            last, eurusd = (quote[0] + quote[1]) / 2, (eur[0] + eur[1]) / 2
        inst = cfg.instrument(symbol)
        try:
            size = position_size(service.tracker.capital_eur(), pct_value, abs(last - stop), inst, eurusd)
        except ValueError as exc:
            await interaction.followup.send(embed=embeds.simple_embed("No se puede calcular", str(exc), embeds.AMBER))
            return
        embed = embeds.simple_embed(
            f"💶 Tamaño de posicion — {symbol}",
            f"Precio actual `{embeds.price(last, inst.digits)}` · stop `{embeds.price(stop, inst.digits)}`\n"
            f"Distancia: {embeds.price(abs(last - stop), inst.digits)}\n"
            f"Capital {service.tracker.capital_eur():.2f} € · riesgo {pct_value:.1f}% = "
            f"{size.allowed_risk_eur:.2f} €\n\n{'✅' if size.fits else '⚠️'} **{embeds.esc(size.note)}**",
            embeds.GREEN if size.fits else embeds.AMBER,
        )
        await interaction.followup.send(embed=embed)

    @tree.command(name="calendario", description="Noticias de alto impacto USD/EUR")
    async def calendario(interaction: discord.Interaction) -> None:
        if not await guard(interaction):
            return
        await interaction.response.defer(thinking=True)
        await bot.run_blocking(bot.service.calendar.refresh)
        events = bot.service.calendar.upcoming(cfg.signals.news_currencies, hours=24 * 8)
        await interaction.followup.send(
            embed=embeds.calendar_embed(events, cfg, failed=bot.service.calendar.last_error is not None)
        )

    @tree.command(name="capital", description="Fija tu capital de referencia en euros")
    async def capital(interaction: discord.Interaction, euros: float) -> None:
        if not await guard(interaction, admin=True):
            return
        if euros <= 0:
            await interaction.response.send_message("El capital tiene que ser positivo.", ephemeral=True)
            return
        bot.service.tracker.set_setting("capital_eur", str(euros))
        await interaction.response.send_message(f"✅ Capital de referencia: **{euros:.2f} €**")

    @tree.command(name="riesgo_pct", description="Fija tu % de riesgo por operacion")
    async def riesgo_pct(interaction: discord.Interaction, porcentaje: float) -> None:
        if not await guard(interaction, admin=True):
            return
        if not 0 < porcentaje <= 10:
            await interaction.response.send_message("Usa un valor entre 0 y 10.", ephemeral=True)
            return
        bot.service.tracker.set_setting("risk_pct", str(porcentaje))
        await interaction.response.send_message(f"✅ Riesgo por operacion: **{porcentaje:.1f}%**")

    @tree.command(name="modo", description="estricto (solo lo validado) o informativo (tambien setups sin ventaja)")
    @app_commands.choices(
        modo=[
            app_commands.Choice(name="estricto — solo señales validadas", value="strict"),
            app_commands.Choice(name="informativo — tambien setups sin ventaja validada", value="informative"),
        ]
    )
    async def modo(interaction: discord.Interaction, modo: app_commands.Choice[str]) -> None:
        if not await guard(interaction, admin=True):
            return
        bot.service.tracker.set_setting("mode", modo.value)
        extra = (
            ""
            if modo.value == "strict"
            else "\n⚠️ Recibiras setups SIN ventaja demostrada, marcados como tales. Mas mensajes, no mas fiables."
        )
        await interaction.response.send_message(f"✅ Modo **{modo.name.split(' —')[0]}**.{extra}")

    @tree.command(name="silenciar", description="Silencia las señales nuevas de un instrumento unas horas")
    @app_commands.choices(instrumento=symbol_choices)
    async def silenciar(
        interaction: discord.Interaction, instrumento: app_commands.Choice[str], horas: float = 4.0
    ) -> None:
        if not await guard(interaction, admin=True):
            return
        if horas <= 0:
            await interaction.response.send_message("Las horas tienen que ser positivas.", ephemeral=True)
            return
        until = datetime.now(UTC) + timedelta(hours=horas)
        bot.service.tracker.set_setting(f"mute_{instrumento.value}", until.isoformat())
        await interaction.response.send_message(
            f"🔕 {instrumento.value} silenciado hasta {embeds.when(until)}. El seguimiento de las abiertas sigue."
        )

    @tree.command(name="estado", description="Salud del sistema: datos, fuente, modelo, interruptor")
    async def estado(interaction: discord.Interaction) -> None:
        if not await guard(interaction):
            return
        await interaction.response.defer(thinking=True)
        health = await bot.run_blocking(bot.service.health)
        await interaction.followup.send(embed=embeds.status_embed(health, cfg))

    @tree.command(name="reactivar", description="Reactiva el bot tras el interruptor de seguridad")
    async def reactivar(interaction: discord.Interaction) -> None:
        if not await guard(interaction, admin=True):
            return
        active, reason = bot.service.tracker.kill_switch_status()
        if not active:
            await interaction.response.send_message("El interruptor de seguridad no esta activo.", ephemeral=True)
            return
        bot.service.tracker.reset_kill_switch()
        logger.warning("Kill switch reactivado manualmente (motivo previo: %s)", reason)
        await interaction.response.send_message(
            f"✅ Reactivado. Se habia detenido por: {reason}\nRevisa `/stats` antes de seguir."
        )

    @tree.command(name="panel", description="Vuelve a crear el panel fijo del canal")
    async def panel(interaction: discord.Interaction) -> None:
        if not await guard(interaction, admin=True):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        await bot.update_panel(force_new=True)
        await interaction.followup.send("✅ Panel recreado.", ephemeral=True)

    @tree.command(name="ayuda", description="Como funciona el bot y que significa cada cosa")
    async def ayuda(interaction: discord.Interaction) -> None:
        if not await guard(interaction):
            return
        service = bot.service
        validated = len(service.artifacts.validation.get("selection", {})) if service.artifacts else 0
        await interaction.response.send_message(embed=embeds.help_embed(cfg, validated, service.artifacts is not None))

    @tree.error
    async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        logger.error("Error en comando", exc_info=error)
        message = "⚠️ Error interno. Queda registrado en logs/bot.log."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def run_bot() -> int:
    setup_logging("bot")
    service = BotService()
    token = service.secrets.discord_bot_token
    if not token:
        raise SystemExit(
            "Falta DISCORD_BOT_TOKEN en .env.\n"
            "1) https://discord.com/developers/applications → New Application → Bot → Reset Token.\n"
            "2) Pega el token en .env y vuelve a arrancar."
        )
    bot = FinanceBot(service)
    logger.info("Arrancando bot de Discord")
    updater.boot_guard_start()  # si una actualizacion reciente no arranca, se revierte sola
    try:
        bot.run(token, log_handler=None)  # el logging ya esta configurado por setup_logging
    except discord.LoginFailure:
        raise SystemExit(
            "Discord ha rechazado el token. Genera uno nuevo en el portal de desarrolladores "
            "(Bot -> Reset Token) y pegalo en DISCORD_BOT_TOKEN del .env."
        ) from None
    except discord.PrivilegedIntentsRequired:
        raise SystemExit(
            "Discord pide intents privilegiados. Este bot no los necesita: revisa que no los hayas activado "
            "a medias en el portal (pestaña Bot)."
        ) from None
    if bot.restart_requested:
        updater.schedule_restart()
        return updater.EXIT_RESTART
    return 0
