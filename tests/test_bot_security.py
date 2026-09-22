"""El bot solo obedece en el servidor autorizado, solo deja tocar los ajustes a
los administradores y se niega a arrancar sin token."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from finance_bot.config import Secrets
from finance_bot.discord_bot import app as bot_app


def _bot(cfg, guild_ids: set[int], admins: str = "") -> bot_app.FinanceBot:
    """Bot real pero sin conectar: basta para permisos y registro de comandos."""
    service = SimpleNamespace(
        cfg=cfg,
        secrets=Secrets(discord_bot_token="x", discord_admin_user_ids=admins),
        artifacts=None,
        tracker=None,
    )
    bot = bot_app.FinanceBot(service)  # type: ignore[arg-type]
    bot.allowed_guild_ids = guild_ids
    return bot


def _interaction(guild_id: int | None) -> SimpleNamespace:
    return SimpleNamespace(guild_id=guild_id, user=SimpleNamespace(id=7))


def test_other_servers_are_rejected(cfg) -> None:
    bot = _bot(cfg, {111})
    assert bot.is_allowed(_interaction(111))
    assert not bot.is_allowed(_interaction(222))


def test_direct_messages_are_rejected(cfg) -> None:
    # sin guild_id es un mensaje directo: fuera del servidor no se responde
    assert not _bot(cfg, {111}).is_allowed(_interaction(None))


def test_admins_restrict_settings_when_configured(cfg) -> None:
    assert _bot(cfg, {111}, admins="7").is_admin(7)
    assert not _bot(cfg, {111}, admins="8, 9").is_admin(7)


def test_without_admin_list_anyone_in_the_server_can_configure(cfg) -> None:
    assert _bot(cfg, {111}).is_admin(12345)


def test_secrets_parse_admin_ids() -> None:
    assert Secrets(discord_admin_user_ids=" 123, 456 ,").admin_user_ids == {123, 456}


def test_refuses_to_start_without_token(monkeypatch) -> None:
    monkeypatch.setattr(bot_app, "setup_logging", lambda name: None)
    monkeypatch.setattr(bot_app, "BotService", lambda: SimpleNamespace(secrets=Secrets(discord_bot_token="")))
    with pytest.raises(SystemExit, match="DISCORD_BOT_TOKEN"):
        bot_app.run_bot()


def test_mentions_only_configured_admins(cfg) -> None:
    assert _bot(cfg, {111}, admins="5,6").mention_admins() == "<@5> <@6>"
    assert _bot(cfg, {111}).mention_admins() == ""


def test_all_commands_register_and_fit_discord_limits(cfg) -> None:
    """Un nombre o una descripcion invalidos solo darian la cara al sincronizar
    con Discord, ya en produccion: se comprueban aqui."""
    bot = _bot(cfg, {111})
    bot_app.register_commands(bot)

    names = {c.name for c in bot.tree.get_commands()}
    assert {"analisis", "senales", "stats", "validacion", "riesgo", "calendario", "estado", "ayuda"} <= names
    assert {"capital", "riesgo_pct", "modo", "silenciar", "reactivar", "panel"} <= names
    for command in bot.tree.get_commands():
        assert command.name == command.name.lower() and len(command.name) <= 32
        assert 1 <= len(command.description) <= 100, command.name


def test_buttons_carry_stable_ids() -> None:
    ids = [item.custom_id for item in bot_app.signal_view("XAUUSD").children]
    assert ids == ["fb:analisis:XAUUSD", "fb:mute:XAUUSD:4"]
    panel_ids = [item.custom_id for item in bot_app.panel_view(["XAUUSD", "EURUSD"]).children]
    assert panel_ids == ["fb:analisis:XAUUSD", "fb:analisis:EURUSD", "fb:senales", "fb:stats:30d"]


def test_blank_optional_values_in_env_do_not_break_startup() -> None:
    """El .env se rellena a mano: los opcionales se quedan vacios."""
    secrets = Secrets(discord_bot_token="x", discord_guild_id="", discord_channel_id="", mt5_login="")
    assert secrets.discord_guild_id == 0 and secrets.discord_channel_id == 0 and secrets.mt5_login == 0


def test_logging_survives_without_console(monkeypatch, tmp_path) -> None:
    """La tarea programada arranca con pythonw.exe: sin consola, sys.stderr es
    None y un handler de consola reventaria en cada linea."""
    import logging

    from finance_bot import logging_setup

    monkeypatch.setattr(logging_setup.sys, "stderr", None)
    monkeypatch.setattr(logging_setup, "PROJECT_ROOT", tmp_path)
    logging_setup.setup_logging("prueba")
    handlers = logging.getLogger().handlers
    assert handlers and not any(type(h) is logging.StreamHandler for h in handlers)
    logging.getLogger(__name__).info("no debe fallar")


def test_startup_message_only_the_first_time(cfg, monkeypatch) -> None:
    """Reiniciar no es noticia: no hay que llenar el canal de "bot arrancado"."""
    import asyncio
    from types import SimpleNamespace

    bot = _bot(cfg, {111})
    sent: list[str] = []
    stored: dict[str, str] = {}
    bot.service = SimpleNamespace(  # type: ignore[assignment]
        cfg=cfg,
        secrets=Secrets(discord_bot_token="x"),
        artifacts=SimpleNamespace(validation={"selection": {}}),
        tracker=SimpleNamespace(get_setting=lambda key, default=None: stored.get(key, default)),
    )
    monkeypatch.setattr(bot, "send", lambda embed, *a, **k: sent.append(embed.title) or asyncio.sleep(0))
    monkeypatch.setattr(bot, "update_panel", lambda *a, **k: asyncio.sleep(0))

    asyncio.run(bot._announce())
    assert len(sent) == 1  # primera vez: se presenta

    stored[bot_app.PANEL_SETTING] = "123"
    asyncio.run(bot._announce())
    assert len(sent) == 1  # ya hay panel: silencio


def test_ping_follows_notification_config(cfg) -> None:
    bot = _bot(cfg, {111}, admins="5")
    assert bot.ping_content() == "@here"  # por defecto avisa a todos los conectados

    bot.service.cfg = cfg.model_copy(
        update={"notifications": cfg.notifications.model_copy(update={"mention": "admins"})}
    )
    assert bot.ping_content() == "<@5>"

    bot.service.cfg = cfg.model_copy(update={"notifications": cfg.notifications.model_copy(update={"mention": "none"})})
    assert bot.ping_content() is None
