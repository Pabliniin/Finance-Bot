"""Dimensionamiento por riesgo fijo: tamaño = (capital x riesgo%) / distancia
al stop. Formula exigida explicitamente por el proyecto, sin variantes.

Limitacion conocida y deliberadamente NO resuelta con una suposicion
silenciosa: el P&L de un par se genera en su divisa de cotizacion (p.ej. USD
para EURUSD, JPY para USDJPY), pero el capital de referencia esta en EUR. Para
convertir correctamente hay que multiplicar por el tipo de cambio
EUR/divisa_cotizacion en el momento de la operacion. Esta funcion EXIGE que
ese tipo de cambio se le pase (`quote_to_eur_rate`); no asume 1:1 por
defecto porque para pares como GBPJPY eso daria un tamaño de posicion
groseramente erroneo. Quien orqueste el backtest o la señal en vivo
(backtest/engine.py, scheduler/daily_job.py) es responsable de resolver ese
tipo de cambio a partir de los propios datos de mercado (p.ej. la ultima vela
de EURUSD/EURJPY/etc. en cache) antes de llamar aqui.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

STANDARD_LOT_UNITS = 100_000


class PositionSizingError(ValueError):
    pass


@dataclass(frozen=True)
class PositionSize:
    lots: float
    units: float
    risk_eur: float
    stop_distance_price: float


def compute_position_size(
    capital_eur: float,
    risk_pct: float,
    entry_price: float,
    stop_loss_price: float,
    quote_to_eur_rate: float,
    min_lot: float = 0.01,
    lot_step: float = 0.01,
    max_lot: float | None = None,
) -> PositionSize:
    if capital_eur <= 0:
        raise PositionSizingError("capital_eur debe ser positivo")
    if not (0 < risk_pct <= 100):
        raise PositionSizingError("risk_pct debe estar en (0, 100]")
    if quote_to_eur_rate <= 0:
        raise PositionSizingError("quote_to_eur_rate debe ser positivo")

    stop_distance = abs(entry_price - stop_loss_price)
    if stop_distance <= 0:
        raise PositionSizingError("stop_loss_price no puede ser igual a entry_price")

    risk_eur = capital_eur * (risk_pct / 100)

    # riesgo_eur = unidades * distancia_al_stop(en divisa de cotizacion) * tipo_cambio_a_eur
    units_raw = risk_eur / (stop_distance * quote_to_eur_rate)
    lots_raw = units_raw / STANDARD_LOT_UNITS

    lots = math.floor(lots_raw / lot_step) * lot_step
    lots = round(lots, 2)

    if lots < min_lot:
        # El riesgo permitido no alcanza ni para el lote minimo del broker:
        # no se redondea hacia arriba (eso rompería el limite de riesgo), se
        # rechaza la señal. Quien llama debe tratarlo como "sin operar".
        raise PositionSizingError(
            f"riesgo disponible ({risk_eur:.2f} EUR) no alcanza el lote minimo "
            f"({min_lot}) para una distancia de stop de {stop_distance}"
        )
    if max_lot is not None:
        lots = min(lots, max_lot)

    actual_units = lots * STANDARD_LOT_UNITS
    actual_risk_eur = actual_units * stop_distance * quote_to_eur_rate

    return PositionSize(
        lots=lots,
        units=actual_units,
        risk_eur=actual_risk_eur,
        stop_distance_price=stop_distance,
    )
