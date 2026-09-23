"""Configuracion: config/settings.yaml (no sensible, versionado) + .env
(secretos, nunca versionado). Validada con pydantic para fallar al arrancar,
no a mitad de un escaneo, si algo esta mal escrito.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"

Timeframe = Literal["M1", "M15", "H1", "H4", "D1"]
TIMEFRAMES: tuple[Timeframe, ...] = ("M1", "M15", "H1", "H4", "D1")


class CostSpec(BaseModel):
    kind: Literal["pct", "abs"]
    value: float = Field(ge=0)

    def price_units(self, price: float) -> float:
        return price * self.value / 100 if self.kind == "pct" else self.value


class SwapSpec(BaseModel):
    long: float
    short: float


class InstrumentConfig(BaseModel):
    digits: int
    contract_size: float
    dukascopy_divisor: float
    spread: CostSpec
    slippage: CostSpec
    swap_per_night: SwapSpec
    min_lot: float
    lot_step: float


class AccountConfig(BaseModel):
    capital_eur: float = Field(gt=0)
    risk_pct: float = Field(gt=0, le=100)
    max_open_signals: int = Field(ge=1)


class TimeframePlan(BaseModel):
    max_bars: int = Field(ge=1)
    sl_atr_min: float = Field(gt=0)
    sl_atr_max: float = Field(gt=0)
    swing_lookback: int = Field(ge=2)


class SignalsConfig(BaseModel):
    mode: Literal["strict", "informative"]
    min_probability_tp1: float = Field(ge=0, le=1)
    min_expected_r: float
    min_similar_cases: int = Field(ge=1)
    news_blackout_hours: dict[str, float]
    news_currencies: list[str]
    # Profundidad de la zona de entrada, en fraccion de 1R. La referencia (lo que
    # se valido) es el extremo peor; entrar mas adentro mejora el precio pero
    # puede no ejecutarse nunca.
    entry_zone_r: float = Field(default=0.2, ge=0, le=0.5)
    # Objetivo extra que se muestra ademas de los dos del plan, en R.
    extra_target_r: float = Field(default=3.0, gt=0)
    # Confluencia minima (votos netos a favor sobre 20) para emitir. Sube el numero
    # para menos señales y mas fuertes; bajalo para ver mas.
    min_confluence: int = Field(default=8, ge=0, le=20)
    # Emitir solo la mejor señal de cada instrumento por escaneo (no varias a la vez).
    one_signal_per_symbol: bool = True
    # Tras una señal, no se emite otra del mismo instrumento en estos minutos (evita
    # que M1 sature). 0 lo desactiva.
    cooldown_minutes: int = Field(default=15, ge=0)


class SessionsConfig(BaseModel):
    intraday_timeframes: list[str]
    allowed_hours: list[int]


class DataConfig(BaseModel):
    history_start: str
    m1_history_start: str
    store_dir: str
    live_source: Literal["auto", "mt5", "dukascopy"]

    @property
    def store_path(self) -> Path:
        path = Path(self.store_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path


class ResearchConfig(BaseModel):
    first_test_year: int
    validation_end: str
    validation_end_overrides: dict[str, str] = Field(default_factory=dict)
    retrain_every_months: int = Field(ge=1)
    random_baseline_iterations: int = Field(ge=10)
    significance_alpha: float = Field(gt=0, lt=1)


class ScheduleConfig(BaseModel):
    scan_every_minutes: int = Field(ge=1)
    daily_report_utc: str
    weekly_report_weekday: int = Field(ge=0, le=6)


class NotificationsConfig(BaseModel):
    """A quien se avisa cuando salta algo importante (señal nueva, aviso de
    cierre urgente, interruptor de seguridad)."""

    mention: Literal["here", "admins", "none"] = "here"


class KillSwitchConfig(BaseModel):
    min_signals: int
    max_drawdown_r: float
    calibration_alpha: float


class RiskConfig(BaseModel):
    kill_switch: KillSwitchConfig


class ExitPlan(BaseModel):
    targets_r: list[float]
    partial: float = Field(ge=0, le=1)
    max_bars_mult: float = Field(gt=0)

    @field_validator("targets_r")
    @classmethod
    def _two_increasing_targets(cls, value: list[float]) -> list[float]:
        if len(value) != 2 or not 0 < value[0] < value[1]:
            raise ValueError("targets_r debe tener exactamente dos objetivos crecientes, p.ej. [1.0, 2.0]")
        return value

    def max_bars(self, plan: TimeframePlan) -> int:
        return max(1, round(plan.max_bars * self.max_bars_mult))

    def targets(self) -> tuple[float, float]:
        return self.targets_r[0], self.targets_r[1]


class AppConfig(BaseModel):
    account: AccountConfig
    instruments: dict[str, InstrumentConfig]
    timeframes: dict[str, TimeframePlan]
    plans: dict[str, ExitPlan]
    signals: SignalsConfig
    sessions_utc: SessionsConfig
    data: DataConfig
    research: ResearchConfig
    schedule: ScheduleConfig
    risk: RiskConfig
    notifications: NotificationsConfig = NotificationsConfig()
    disclaimer: str
    # Version corta para el pie de cada mensaje de Discord (los mensajes tienen
    # que poder leerse de un vistazo, pero sin perder el aviso).
    disclaimer_short: str = "Estimacion estadistica, no una garantia · No es asesoramiento financiero · Tu ejecutas"

    @field_validator("timeframes")
    @classmethod
    def _known_timeframes(cls, value: dict[str, TimeframePlan]) -> dict[str, TimeframePlan]:
        unknown = set(value) - set(TIMEFRAMES)
        if unknown:
            raise ValueError(f"temporalidades desconocidas: {unknown}. Validas: {TIMEFRAMES}")
        return value

    def instrument(self, symbol: str) -> InstrumentConfig:
        try:
            return self.instruments[symbol]
        except KeyError as exc:
            raise KeyError(f"instrumento no configurado: {symbol}. Disponibles: {list(self.instruments)}") from exc


class Secrets(BaseSettings):
    """Todo lo sensible. Sin valores por defecto para nada que sea un secreto."""

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    discord_bot_token: str = ""
    # Opcionales: si los dejas vacios el bot se ata al servidor donde esta al
    # arrancar y elige el primer canal donde puede escribir (y te lo dice).
    discord_guild_id: int = 0
    discord_channel_id: int = 0
    discord_admin_user_ids: str = ""  # quien puede cambiar ajustes; vacio = cualquiera del servidor
    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_terminal_path: str = ""

    @field_validator("discord_guild_id", "discord_channel_id", "mt5_login", mode="before")
    @classmethod
    def _blank_means_unset(cls, value: object) -> object:
        """En el .env se dejan vacios los opcionales: vacio = 0 = sin fijar."""
        return 0 if isinstance(value, str) and not value.strip() else value

    @property
    def admin_user_ids(self) -> set[int]:
        """Vacio = cualquiera del servidor autorizado puede tocar los ajustes."""
        return {int(chunk) for chunk in self.discord_admin_user_ids.split(",") if chunk.strip()}


@lru_cache
def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path) if path else CONFIG_PATH
    with open(config_path, encoding="utf-8") as fh:
        return AppConfig.model_validate(yaml.safe_load(fh))


def load_secrets() -> Secrets:
    return Secrets()
