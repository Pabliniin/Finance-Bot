"""Reconexion con MetaTrader 5: cuando la tuberia con el terminal se rompe
(terminal cerrado o reiniciandose) todo devuelve None con un error de IPC y el
bot tiene que reconectar solo, no quedarse a ciegas hasta el siguiente reinicio."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from finance_bot.config import Secrets
from finance_bot.data import live


class FakeMT5:
    """Simula el modulo MetaTrader5: falla con IPC hasta que se reinicializa."""

    TIMEFRAME_M1 = 1

    def __init__(self, fail_first: bool = True) -> None:
        self.fail_first = fail_first
        self.initialized = 0
        self.shutdowns = 0
        self._error = (1, "Success")

    def initialize(self, *args, **kwargs) -> bool:
        self.initialized += 1
        self.fail_first = self.fail_first and self.initialized < 2
        return True

    def shutdown(self) -> None:
        self.shutdowns += 1

    def last_error(self):
        return self._error

    def symbol_info(self, name):
        return SimpleNamespace(swap_long=0.0, swap_short=0.0)

    def symbol_select(self, name, enable):
        return True

    def symbol_info_tick(self, name):
        if self.fail_first:
            self._error = (-10001, "IPC send failed")
            return None
        self._error = (1, "Success")
        return SimpleNamespace(bid=100.0, ask=100.2, time=0)


@pytest.fixture
def feed(monkeypatch) -> tuple[live.MT5Feed, FakeMT5]:
    fake = FakeMT5()
    monkeypatch.setitem(__import__("sys").modules, "MetaTrader5", fake)
    mt5_feed = live.MT5Feed(Secrets(discord_bot_token="x"), ["XAUUSD"])
    return mt5_feed, fake


def test_ipc_failure_triggers_one_reconnect_and_succeeds(feed) -> None:
    mt5_feed, fake = feed
    assert fake.initialized == 1
    assert mt5_feed.quote("XAUUSD") == (100.0, 100.2)
    assert fake.shutdowns == 1 and fake.initialized == 2  # reconecto una vez


def test_non_ipc_none_is_not_retried(feed) -> None:
    mt5_feed, fake = feed
    fake.fail_first = False
    fake.symbol_info_tick = lambda name: None  # type: ignore[method-assign]
    fake._error = (-2, "Invalid params")
    assert mt5_feed.quote("XAUUSD") is None
    assert fake.initialized == 1  # sin reconexion: no era un fallo de conexion
