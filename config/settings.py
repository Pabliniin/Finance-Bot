"""Configuracion SENSIBLE, leida exclusivamente de variables de entorno (.env).
Todo lo que no es sensible (riesgo, universo, horarios) vive en config.yaml y
se carga con config/loader.py. Esta separacion es deliberada: settings.py
nunca deberia poder acabar commiteado con un secreto dentro por accidente,
porque no tiene valores por defecto para nada sensible.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    database_url_host: str = ""  # usado solo por el collector nativo de Windows (fuera de Docker)
    environment: str = "development"
    log_level: str = "INFO"

    discord_bot_token: str = ""
    discord_guild_id: str = ""
    discord_alerts_channel_id: str = ""
    discord_reports_channel_id: str = ""

    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_terminal_path: str = ""


def get_settings() -> Settings:
    # Sin cache: Settings() es barato y esto evita sorpresas en tests que
    # cambian variables de entorno entre casos.
    return Settings()
