"""Calendario economico: filtra bien las noticias (mueve los blackouts que
BLOQUEAN señales) y, si el feed gratuito falla, lo dice en vez de inventar que
no hay eventos."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import requests

from finance_bot.data import calendar
from finance_bot.data.calendar import EconomicCalendar, EconomicEvent

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _cal(events: list[EconomicEvent]) -> EconomicCalendar:
    cal = EconomicCalendar()
    cal._events = events
    return cal


def test_upcoming_filters_by_currency_impact_and_window() -> None:
    cal = _cal(
        [
            EconomicEvent(NOW + timedelta(hours=1), "USD", "NFP", "High", "", ""),
            EconomicEvent(NOW + timedelta(hours=2), "JPY", "otra moneda", "High", "", ""),
            EconomicEvent(NOW + timedelta(hours=3), "USD", "impacto bajo", "Low", "", ""),
            EconomicEvent(NOW + timedelta(hours=50), "USD", "fuera de ventana", "High", "", ""),
            EconomicEvent(NOW - timedelta(hours=1), "USD", "ya pasada", "High", "", ""),
        ]
    )
    up = cal.upcoming(["USD", "EUR"], hours=24, now=NOW)
    assert [e.title for e in up] == ["NFP"]


def test_refresh_failure_reports_error_without_inventing_events(monkeypatch) -> None:
    def boom(*a, **k):
        raise requests.RequestException("feed caido")

    monkeypatch.setattr(calendar.requests, "get", boom)
    cal = EconomicCalendar()
    cal.refresh(force=True)
    assert cal.last_error is not None
    assert cal.upcoming(["USD"], hours=72, now=NOW) == []


def test_refresh_parses_and_dedupes_events(monkeypatch) -> None:
    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> list:
            return [{"date": "2026-09-24T12:30:00-04:00", "country": "USD", "title": "NFP", "impact": "High"}]

    monkeypatch.setattr(calendar.requests, "get", lambda *a, **k: _Resp())
    cal = EconomicCalendar()
    cal.refresh(force=True)
    assert cal.last_error is None
    # los dos feeds (esta semana / siguiente) devuelven el mismo evento -> se deduplica
    assert len(cal._events) == 1
    e = cal._events[0]
    assert e.currency == "USD" and e.title == "NFP" and e.time.tzinfo is not None
