"""Feed de OANDA (tiempo real en Linux, sin MT5): parsea velas BID y cotizacion
con la API simulada, excluye la vela en formacion y falla claro si el token no vale."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
import requests

from finance_bot.config import Secrets
from finance_bot.data import live


class _Resp:
    def __init__(self, payload: dict, status: int = 200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _Session:
    def __init__(self, routes: dict):
        self.routes, self.headers = routes, {}

    def get(self, url: str, params=None, timeout=None):
        for key, resp in self.routes.items():  # el orden de insercion decide la prioridad
            if key in url:
                return resp
        return _Resp({}, 404)


def _feed(monkeypatch, routes: dict) -> live.OandaFeed:
    routes.setdefault("/v3/accounts", _Resp({"accounts": [{"id": "acc"}]}))  # _check_auth
    monkeypatch.setattr(live.requests, "Session", lambda: _Session(routes))
    secrets = Secrets(oanda_api_token="tok", oanda_account_id="acc", oanda_env="practice")
    return live.OandaFeed(secrets, ["XAUUSD", "EURUSD"])


def _candle(time: str, o, h, low, c, complete=True):
    bid = {"o": str(o), "h": str(h), "l": str(low), "c": str(c)}
    return {"time": time, "complete": complete, "volume": 7, "bid": bid}


def test_fetch_m1_parses_bid_candles_and_drops_the_forming_one(monkeypatch) -> None:
    candles = {
        "candles": [
            _candle("2026-10-20T12:00:00.000000000Z", 4000.0, 4001.0, 3999.0, 4000.5),
            _candle("2026-10-20T12:01:00.000000000Z", 4000.5, 4002.0, 4000.0, 4001.5),
            _candle("2026-10-20T12:02:00.000000000Z", 4001.5, 4001.6, 4001.4, 4001.5, complete=False),
        ]
    }
    feed = _feed(monkeypatch, {"/candles": _Resp(candles)})
    bars, complete_until = feed.fetch_m1("XAUUSD", datetime(2026, 10, 20, 11, 0, tzinfo=UTC))
    assert list(bars.index) == [pd.Timestamp("2026-10-20 12:00", tz="UTC"), pd.Timestamp("2026-10-20 12:01", tz="UTC")]
    assert bars["close"].tolist() == [4000.5, 4001.5]
    assert bars["high"].tolist() == [4001.0, 4002.0]
    assert complete_until == pd.Timestamp("2026-10-20 12:02", tz="UTC")  # ultima COMPLETA + 1 min


def test_fetch_m1_without_candles_keeps_completion_where_it_was(monkeypatch) -> None:
    feed = _feed(monkeypatch, {"/candles": _Resp({"candles": []})})
    since = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)
    bars, complete_until = feed.fetch_m1("EURUSD", since)
    assert bars.empty and complete_until == pd.Timestamp(since)


def test_quote_parses_bid_and_ask(monkeypatch) -> None:
    pricing = {"prices": [{"instrument": "XAU_USD", "bids": [{"price": "4000.0"}], "asks": [{"price": "4000.4"}]}]}
    feed = _feed(monkeypatch, {"/pricing": _Resp(pricing)})
    assert feed.quote("XAUUSD") == (4000.0, 4000.4)


def test_bad_token_raises_so_it_can_fall_back(monkeypatch) -> None:
    monkeypatch.setattr(live.requests, "Session", lambda: _Session({"/v3/accounts": _Resp({}, 401)}))
    with pytest.raises(live.LiveFeedError):
        live.OandaFeed(Secrets(oanda_api_token="malo"), ["XAUUSD"])


def test_create_feed_uses_oanda_when_configured(monkeypatch, cfg) -> None:
    monkeypatch.setattr(live.requests, "Session", lambda: _Session({"/v3/accounts": _Resp({"accounts": []})}))
    local = cfg.model_copy(update={"data": cfg.data.model_copy(update={"live_source": "oanda"})})
    feed = live.create_feed(local, Secrets(oanda_api_token="tok", oanda_account_id="acc"))
    assert isinstance(feed, live.OandaFeed) and feed.realtime
