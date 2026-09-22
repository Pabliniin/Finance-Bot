"""Tamaño de posicion por riesgo fijo: lotes = riesgo / (distancia al stop x
tamaño de contrato), con conversion EUR -> USD (XAUUSD y EURUSD liquidan en
USD). Nunca se redondea hacia arriba: si el riesgo permitido no llega al lote
minimo del broker, se dice claramente cuanto arriesgaria ese lote minimo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from finance_bot.config import InstrumentConfig


@dataclass(frozen=True)
class PositionSize:
    lots: float  # 0.0 si ni el lote minimo cabe en el riesgo permitido
    risk_eur: float  # riesgo real con `lots` (o con el lote minimo si lots == 0)
    risk_pct: float  # riesgo real en % del capital
    allowed_risk_eur: float
    fits: bool
    note: str


def position_size(
    capital_eur: float,
    risk_pct: float,
    stop_distance: float,
    inst: InstrumentConfig,
    eurusd_rate: float,
) -> PositionSize:
    if capital_eur <= 0 or risk_pct <= 0:
        raise ValueError("capital y riesgo deben ser positivos")
    if stop_distance <= 0 or not math.isfinite(stop_distance):
        raise ValueError("la distancia al stop debe ser positiva")
    if eurusd_rate <= 0:
        raise ValueError("tipo de cambio EURUSD invalido")

    allowed_eur = capital_eur * risk_pct / 100
    risk_usd_per_lot = stop_distance * inst.contract_size
    risk_eur_per_lot = risk_usd_per_lot / eurusd_rate
    raw_lots = allowed_eur / risk_eur_per_lot
    lots = math.floor(raw_lots / inst.lot_step + 1e-9) * inst.lot_step
    lots = round(lots, 2)

    if lots < inst.min_lot:
        min_risk = inst.min_lot * risk_eur_per_lot
        return PositionSize(
            lots=0.0,
            risk_eur=min_risk,
            risk_pct=100 * min_risk / capital_eur,
            allowed_risk_eur=allowed_eur,
            fits=False,
            note=(
                f"El lote minimo ({inst.min_lot}) arriesga {min_risk:.2f} EUR "
                f"({100 * min_risk / capital_eur:.1f}% del capital), por encima de tu limite de "
                f"{allowed_eur:.2f} EUR ({risk_pct:.1f}%). No recomendable con este capital."
            ),
        )
    real = lots * risk_eur_per_lot
    return PositionSize(
        lots=lots,
        risk_eur=real,
        risk_pct=100 * real / capital_eur,
        allowed_risk_eur=allowed_eur,
        fits=True,
        note=f"{lots:.2f} lotes arriesgan {real:.2f} EUR ({100 * real / capital_eur:.1f}% del capital).",
    )
