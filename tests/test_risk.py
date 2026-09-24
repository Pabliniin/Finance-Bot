from __future__ import annotations

import pytest

from finance_bot.risk import position_size


def test_eurusd_sizing_known_values(cfg) -> None:
    inst = cfg.instrument("EURUSD")
    # 250 EUR, 2% = 5 EUR. Stop de 20 pips (0.0020): 1 lote pierde 200 USD = 200/1.10 EUR
    size = position_size(250, 2.0, 0.0020, inst, eurusd_rate=1.10)
    assert size.fits
    assert size.lots == pytest.approx(0.02)  # 0.0275 redondeado HACIA ABAJO al paso de 0.01
    assert size.risk_eur <= size.allowed_risk_eur + 1e-9


def test_gold_min_lot_exceeds_small_account_risk(cfg) -> None:
    inst = cfg.instrument("XAUUSD")
    # stop de 25 USD: 0.01 lotes (1 onza) arriesgan 25 USD ~ 22.7 EUR > 12.5 EUR (5% de 250)
    size = position_size(250, 5.0, 25.0, inst, eurusd_rate=1.10)
    assert not size.fits and size.lots == 0.0
    assert size.risk_eur == pytest.approx(25.0 / 1.10)
    assert "No recomendable" in size.note
    # da la salida concreta: el capital al que el lote minimo cabria en el 5%
    needed = (25.0 / 1.10) * 100 / 5.0
    assert f"~{needed:.0f} EUR" in size.note


def test_never_rounds_up_beyond_allowed_risk(cfg) -> None:
    inst = cfg.instrument("XAUUSD")
    for stop in (3.0, 7.5, 11.2, 19.9):
        size = position_size(10_000, 1.0, stop, inst, eurusd_rate=1.08)
        assert size.risk_eur <= size.allowed_risk_eur + 1e-9


@pytest.mark.parametrize("args", [(0, 2, 1.0, 1.1), (250, 0, 1.0, 1.1), (250, 2, 0.0, 1.1), (250, 2, 1.0, 0.0)])
def test_invalid_inputs_raise(cfg, args) -> None:
    capital, risk, stop, rate = args
    with pytest.raises(ValueError):
        position_size(capital, risk, stop, cfg.instrument("EURUSD"), rate)
