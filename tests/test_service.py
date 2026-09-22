"""Orquestacion con una fuente de datos simulada: continuidad del refresco y
escaneo completo sin red."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from finance_bot.config import Secrets
from finance_bot.service import BotService
from tests.conftest import random_walk_bars


class FakeFeed:
    name = "falsa"
    realtime = True

    def __init__(self) -> None:
        self.requested: dict[str, pd.Timestamp] = {}

    def fetch_m1(self, symbol: str, since: datetime) -> tuple[pd.DataFrame, pd.Timestamp]:
        self.requested[symbol] = pd.Timestamp(since)
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]), pd.Timestamp.now(tz="UTC").floor("min")

    def quote(self, symbol: str) -> tuple[float, float] | None:
        return None


@pytest.fixture
def service(cfg, tmp_path) -> BotService:
    local_cfg = cfg.model_copy(update={"data": cfg.data.model_copy(update={"store_dir": str(tmp_path)})})
    svc = BotService(cfg=local_cfg, secrets=Secrets(discord_bot_token=""))
    svc.feed = FakeFeed()
    svc.calendar.refresh = lambda force=False: None  # type: ignore[method-assign]  # sin red en los tests
    return svc


def test_refresh_requests_m1_from_end_of_h1_history(service: BotService) -> None:
    h1 = random_walk_bars(24 * 30, freq="1h", start=str(pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=60)))
    service.md.h1_store.write("XAUUSD", h1)
    service.refresh_data()
    requested = service.feed.requested["XAUUSD"]  # type: ignore[union-attr]
    # el M1 debe empezar como tarde donde acaba el H1 (sin hueco entre ambos)
    assert requested <= h1.index.max() + pd.Timedelta(hours=1)


def test_refresh_continues_from_last_m1_bar(service: BotService) -> None:
    now = pd.Timestamp.now(tz="UTC").floor("min")
    m1 = random_walk_bars(120, freq="1min", start=str(now - pd.Timedelta(hours=3)))
    service.md.m1_store.write("EURUSD", m1)
    service.refresh_data()
    assert service.feed.requested["EURUSD"] == m1.index.max() + pd.Timedelta(minutes=1)  # type: ignore[union-attr]


def test_scan_without_data_does_not_crash_or_invent_signals(service: BotService) -> None:
    result = service.scan()
    assert result.errors == [] and result.new_signals == []
    for analysis in result.analyses.values():
        assert any("sin datos" in w for w in analysis.warnings)


def _open_signal(service: BotService, **overrides) -> None:
    """Mete una señal abierta en la base de datos del seguimiento."""
    from tests.test_signals import _signal

    signal = _signal(**overrides)
    service.tracker.record(signal)


def test_exit_advice_is_recorded_once_and_not_repeated(service: BotService, monkeypatch) -> None:
    from datetime import UTC, datetime

    from finance_bot.engine.exits import ExitAdvice

    _open_signal(service)
    advice = ExitAdvice(
        key="k",
        symbol="XAUUSD",
        tf="H1",
        direction=1,
        kind="reversion",
        urgency="alta",
        headline="El contexto se ha girado en contra",
        detail="12/20 en contra",
        r_now=0.4,
    )
    monkeypatch.setattr("finance_bot.engine.exits.evaluate", lambda *a, **k: [advice])
    now = datetime.now(UTC)

    first = service._exit_advice({}, {"XAUUSD": (100.0, 100.2)}, now)
    second = service._exit_advice({}, {"XAUUSD": (100.0, 100.2)}, now)

    assert [a.kind for a in first] == ["reversion"]
    assert second == []  # el mismo aviso no se repite dentro del periodo de espera
    scoreboard_input = service.tracker.first_advice_r("k")
    assert scoreboard_input == (0.4, "El contexto se ha girado en contra")


def test_peak_r_only_grows(service: BotService) -> None:
    _open_signal(service)
    assert service.tracker.update_peak_r("k", 0.8) == 0.8
    assert service.tracker.update_peak_r("k", 0.3) == 0.8
    assert service.tracker.update_peak_r("k", 1.4) == 1.4
