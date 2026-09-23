"""Punto de entrada unico:

python -m finance_bot download     # descarga/actualiza el historico (Dukascopy, reanudable)
python -m finance_bot research     # validacion walk-forward completa + entrena el modelo final
python -m finance_bot scan         # un escaneo ahora mismo, imprime lo que enviaria por Discord
python -m finance_bot run          # arranca el bot de Discord (escaneo automatico + comandos)
python -m finance_bot check        # comprueba configuracion, datos, modelo y conexiones
python -m finance_bot invitar      # enlace para meter el bot en tu servidor de Discord
python -m finance_bot update       # baja y aplica la ultima version del repositorio
"""

from __future__ import annotations

import argparse
import sys
from datetime import date


def _cmd_download(args: argparse.Namespace) -> int:
    from finance_bot.config import load_config
    from finance_bot.data.dukascopy import DukascopyClient, download_h1_history, download_m1_history
    from finance_bot.data.store import BarStore
    from finance_bot.logging_setup import setup_logging

    setup_logging("download")
    cfg = load_config()
    client = DukascopyClient()
    h1_store = BarStore(cfg.data.store_path, "h1")
    m1_store = BarStore(cfg.data.store_path, "m1")
    symbols = args.symbols or list(cfg.instruments)

    def progress(message: str) -> None:
        print(message, flush=True)

    for symbol in symbols:
        divisor = cfg.instrument(symbol).dukascopy_divisor
        if args.resolution in ("h1", "all"):
            start = date.fromisoformat(cfg.data.history_start)
            new = download_h1_history(client, h1_store, symbol, divisor, start, progress=progress)
            print(f"{symbol}: {new} velas H1 nuevas. Ultima: {h1_store.last_timestamp(symbol)}", flush=True)
        if args.resolution in ("m1", "all"):
            start = date.fromisoformat(cfg.data.m1_history_start)
            new = download_m1_history(client, m1_store, symbol, divisor, start, progress=progress)
            print(f"{symbol}: {new} velas M1 nuevas. Ultima: {m1_store.last_timestamp(symbol)}", flush=True)
    return 0


def _cmd_research(args: argparse.Namespace) -> int:
    from finance_bot.research.pipeline import run_research

    return run_research(final=args.final)


def _cmd_scan(args: argparse.Namespace) -> int:
    from finance_bot.service import run_single_scan

    return run_single_scan()


def _cmd_run(args: argparse.Namespace) -> int:
    from finance_bot.discord_bot.app import run_bot

    return run_bot()


def _cmd_update(args: argparse.Namespace) -> int:
    from finance_bot.updater import run_update_command

    return run_update_command()


def _cmd_check(args: argparse.Namespace) -> int:
    from finance_bot.service import run_health_check

    return run_health_check()


def _cmd_invitar(args: argparse.Namespace) -> int:
    """Enlace de invitacion con los permisos justos: leer el canal, escribir,
    embeds, adjuntar ficheros e historial. Nada de administrador."""
    import requests

    from finance_bot.config import load_secrets

    token = load_secrets().discord_bot_token
    if not token:
        print("Primero pon DISCORD_BOT_TOKEN en .env (portal de desarrolladores de Discord).")
        return 1
    try:
        response = requests.get(
            "https://discord.com/api/v10/oauth2/applications/@me",
            headers={"Authorization": f"Bot {token}"},
            timeout=20,
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"No se pudo contactar con Discord: {type(exc).__name__}")
        return 1
    app_id = payload.get("id")
    if not app_id:
        print("Discord rechazo el token. Revisa DISCORD_BOT_TOKEN en .env.")
        return 1
    # Los minimos imprescindibles, ni uno mas:
    permissions = (
        (1 << 10)  # ver el canal
        | (1 << 11)  # enviar mensajes
        | (1 << 13)  # gestionar mensajes (solo para fijar el panel)
        | (1 << 14)  # incrustar embeds
        | (1 << 15)  # adjuntar ficheros
        | (1 << 16)  # leer el historial
        | (1 << 17)  # mencionar @here al avisar de una señal
    )
    print("1) Abre este enlace y elige tu servidor:\n")
    print(
        f"https://discord.com/oauth2/authorize?client_id={app_id}"
        f"&permissions={permissions}&scope=bot%20applications.commands\n"
    )
    print("2) Arranca el bot con: python -m finance_bot run")
    return 0


def _utf8_console() -> None:
    """La consola de Windows usa cp1252 por defecto y revienta al imprimir
    emojis (📈, 🟢...). Se fuerza UTF-8; si aun asi un caracter no cabe, se
    sustituye en vez de abortar."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(
        prog="finance_bot", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_download = sub.add_parser("download", help="descarga/actualiza el historico (Dukascopy), reanudable")
    p_download.add_argument("--symbols", nargs="*", help="por defecto: todos los de config/settings.yaml")
    p_download.add_argument("--resolution", choices=["h1", "m1", "all"], default="all")
    p_download.set_defaults(func=_cmd_download)

    p_research = sub.add_parser("research", help="validacion walk-forward y entrenamiento del modelo")
    p_research.add_argument(
        "--final",
        action="store_true",
        help="evalua tambien el periodo de test reservado. Hazlo UNA vez, con el diseño ya congelado.",
    )
    p_research.set_defaults(func=_cmd_research)

    sub.add_parser("scan", help="un escaneo inmediato sin enviar nada").set_defaults(func=_cmd_scan)
    sub.add_parser("run", help="arranca el bot de Discord").set_defaults(func=_cmd_run)
    sub.add_parser("check", help="comprobacion de salud").set_defaults(func=_cmd_check)
    sub.add_parser("update", help="baja y aplica la ultima version del repositorio").set_defaults(func=_cmd_update)
    sub.add_parser("invitar", help="enlace para meter el bot en tu servidor de Discord").set_defaults(func=_cmd_invitar)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
