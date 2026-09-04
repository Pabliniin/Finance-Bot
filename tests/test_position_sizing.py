from __future__ import annotations

import pytest

from risk.position_sizing import PositionSizingError, compute_position_size


def test_fixed_risk_sizing_matches_expected_formula():
    # capital 250 EUR, riesgo 5% = 12.5 EUR de riesgo.
    # entrada 1.1000, stop 1.0950 -> distancia 0.0050. quote_to_eur_rate=1.0
    # unidades = 12.5 / (0.0050 * 1.0) = 2500 -> 0.025 lotes -> redondeado
    # hacia abajo al step de 0.01 -> 0.02 lotes.
    result = compute_position_size(
        capital_eur=250,
        risk_pct=5,
        entry_price=1.1000,
        stop_loss_price=1.0950,
        quote_to_eur_rate=1.0,
    )
    assert result.lots == pytest.approx(0.02)
    assert result.units == pytest.approx(2000)
    assert result.risk_eur == pytest.approx(10.0)  # <= riesgo maximo permitido (12.5), nunca por encima
    assert result.stop_distance_price == pytest.approx(0.0050)


def test_realized_risk_never_exceeds_requested_risk():
    result = compute_position_size(
        capital_eur=250, risk_pct=5, entry_price=1.1000, stop_loss_price=1.0950, quote_to_eur_rate=1.0
    )
    requested_risk_eur = 250 * 0.05
    assert result.risk_eur <= requested_risk_eur


def test_short_direction_uses_absolute_stop_distance():
    long_result = compute_position_size(
        capital_eur=250, risk_pct=5, entry_price=1.1000, stop_loss_price=1.0950, quote_to_eur_rate=1.0
    )
    short_result = compute_position_size(
        capital_eur=250, risk_pct=5, entry_price=1.1000, stop_loss_price=1.1050, quote_to_eur_rate=1.0
    )
    assert long_result.lots == short_result.lots


def test_rejects_when_available_risk_is_below_broker_minimum_lot():
    with pytest.raises(PositionSizingError):
        compute_position_size(
            capital_eur=250, risk_pct=0.1, entry_price=1.1000, stop_loss_price=1.0900, quote_to_eur_rate=1.0
        )


def test_max_lot_cap_is_respected():
    result = compute_position_size(
        capital_eur=250,
        risk_pct=100,
        entry_price=1.1000,
        stop_loss_price=1.0999,  # distancia minuscula -> lotes muy grandes sin el cap
        quote_to_eur_rate=1.0,
        max_lot=1.0,
    )
    assert result.lots == 1.0


def test_zero_stop_distance_is_rejected():
    with pytest.raises(PositionSizingError):
        compute_position_size(
            capital_eur=250, risk_pct=5, entry_price=1.1000, stop_loss_price=1.1000, quote_to_eur_rate=1.0
        )


def test_invalid_risk_pct_is_rejected():
    with pytest.raises(PositionSizingError):
        compute_position_size(
            capital_eur=250, risk_pct=0, entry_price=1.1000, stop_loss_price=1.0950, quote_to_eur_rate=1.0
        )
    with pytest.raises(PositionSizingError):
        compute_position_size(
            capital_eur=250, risk_pct=150, entry_price=1.1000, stop_loss_price=1.0950, quote_to_eur_rate=1.0
        )


def test_non_positive_quote_to_eur_rate_is_rejected():
    with pytest.raises(PositionSizingError):
        compute_position_size(
            capital_eur=250, risk_pct=5, entry_price=1.1000, stop_loss_price=1.0950, quote_to_eur_rate=0
        )
