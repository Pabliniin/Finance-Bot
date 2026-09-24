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


def test_delayed_feed_is_upgraded_when_mt5_appears(service: BotService, monkeypatch) -> None:
    """El mini PC arranca y MT5 tarda: sin reintento el bot se quedaria con
    datos de una hora de retraso hasta el siguiente reinicio."""
    from datetime import UTC, datetime

    from finance_bot import service as service_module

    class Delayed:
        name, realtime = "Dukascopy (retraso ~1h)", False

    class Live:
        name, realtime = "MT5", True

    service.feed = Delayed()  # type: ignore[assignment]
    service._feed_checked = datetime.now(UTC)
    monkeypatch.setattr(service_module, "create_feed", lambda cfg, secrets: Live())

    assert service._ensure_feed().name == "Dukascopy (retraso ~1h)"  # aun no toca reintentar

    service._feed_checked = datetime.now(UTC) - service_module.FEED_RETRY
    assert service._ensure_feed().name == "MT5"


def test_realtime_feed_is_never_replaced(service: BotService, monkeypatch) -> None:
    from datetime import UTC, datetime

    from finance_bot import service as service_module

    class Live:
        name, realtime = "MT5", True

    service.feed = Live()  # type: ignore[assignment]
    service._feed_checked = datetime.now(UTC) - service_module.FEED_RETRY * 10
    monkeypatch.setattr(service_module, "create_feed", lambda cfg, secrets: pytest.fail("no hace falta reconectar"))
    assert service._ensure_feed().name == "MT5"


def test_live_feed_failure_falls_back_to_delayed_source(service: BotService, monkeypatch) -> None:
    """MT5 se cae a mitad de sesion: el bot sigue con el respaldo retrasado y
    lo dice, en vez de quedarse mudo con un error."""
    from finance_bot import service as service_module
    from finance_bot.data.live import LiveFeedError

    class Broken:
        name, realtime = "MT5", True

        def fetch_m1(self, symbol, since):
            raise LiveFeedError("MT5 no devolvio velas: (-10001, 'IPC send failed')")

    class Delayed(FakeFeed):
        name, realtime = "Dukascopy (retraso ~1h)", False

    service.feed = Broken()  # type: ignore[assignment]
    monkeypatch.setattr(service_module, "DukascopyFeed", lambda cfg: Delayed())

    notice = service.refresh_with_fallback()
    assert notice is not None and "retraso" in notice
    assert service.feed.name.startswith("Dukascopy")


def test_delayed_feed_failure_is_a_real_error(service: BotService) -> None:
    from finance_bot.data.live import LiveFeedError

    class BrokenDelayed:
        name, realtime = "Dukascopy (retraso ~1h)", False

        def fetch_m1(self, symbol, since):
            raise LiveFeedError("sin red")

    service.feed = BrokenDelayed()  # type: ignore[assignment]
    with pytest.raises(LiveFeedError):
        service.refresh_with_fallback()


def test_full_live_days_are_protected_from_the_nightly_download(service: BotService) -> None:
    """Un dia entero escrito por MT5 no debe ser pisado por Dukascopy por la noche."""
    from datetime import UTC, datetime, timedelta

    from tests.conftest import random_walk_bars

    day_full = (datetime.now(UTC) - timedelta(days=3)).date()
    day_partial = (datetime.now(UTC) - timedelta(days=2)).date()
    full = random_walk_bars(1380, freq="1min", start=f"{day_full} 00:00")
    partial = random_walk_bars(200, freq="1min", start=f"{day_partial} 08:00")
    service.md.m1_store.write("XAUUSD", full)
    service.md.m1_store.write("XAUUSD", partial)

    protected = service.protect_live_days("XAUUSD")

    assert day_full.isoformat() in protected
    assert day_partial.isoformat() not in protected  # con huecos, que lo complete Dukascopy
    assert day_full.isoformat() in service.md.m1_store.fetched("XAUUSD")


def test_correlated_exposure_detects_same_dollar_direction() -> None:
    """Largo en oro y largo en EURUSD apuestan los dos por un dolar debil: van
    en el mismo sentido y se avisa. Sentidos opuestos se compensan."""
    from finance_bot.service import correlated_exposure

    assert correlated_exposure("EURUSD", 1, [("XAUUSD", 1)]) == ("XAUUSD", 1)  # ambas: dolar debil
    assert correlated_exposure("EURUSD", -1, [("XAUUSD", 1)]) is None  # se compensan
    assert correlated_exposure("XAUUSD", 1, [("XAUUSD", 1)]) is None  # mismo instrumento, no cuenta
    assert correlated_exposure("EURUSD", 1, []) is None  # sin nada abierto


def test_cooldown_blocks_a_second_signal_too_soon(service: BotService) -> None:
    """Tras una señal, el instrumento descansa el cooldown: evita que M1 sature."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    service.tracker.set_setting("last_signal_XAUUSD", now.isoformat())
    last = service.tracker.last_signal_at("XAUUSD")
    assert last is not None
    cooldown = service.cfg.signals.cooldown_minutes
    assert now - last < timedelta(minutes=cooldown)  # dentro del cooldown
    assert service.tracker.last_signal_at("EURUSD") is None  # otro instrumento, sin cooldown
