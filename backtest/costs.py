"""Modelo de costes: spread, comision, slippage y financiacion overnight
(swap). Todos obligatorios en cada operacion simulada, tal como exige el
proyecto - un backtest sin estos costes sistematicamente sobreestima la
expectativa real.

Los valores por defecto son aproximaciones conservadoras de una cuenta XM
Standard, NO los valores reales. En cuanto el collector tenga acceso a
symbol_info()/order book real de MT5, hay que sustituir los defaults por
datos del broker (ver TODO en data/providers/mt5_provider.py:symbol_info).
Mientras tanto, para no fingir precision que no existe, se sesga el default
hacia "peor de lo esperado" en vez de "mejor de lo esperado": si el bot
sobrevive a costes pesimistas, es mas probable que sobreviva a los reales.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config.instrument_specs import pip_size


@dataclass
class CostModel:
    default_spread_pips: float = 1.8
    spread_pips_overrides: dict[str, float] = field(default_factory=dict)

    commission_per_lot_round_turn_eur: float = 7.0
    slippage_pips: float = 0.5

    # Negativo = coste para el trader (lo habitual salvo carry trade favorable).
    default_swap_per_lot_per_night_eur: float = -2.5
    swap_overrides_long: dict[str, float] = field(default_factory=dict)
    swap_overrides_short: dict[str, float] = field(default_factory=dict)

    def spread_pips_for(self, instrument: str) -> float:
        return self.spread_pips_overrides.get(instrument, self.default_spread_pips)

    def spread_cost_price(self, instrument: str) -> float:
        return self.spread_pips_for(instrument) * pip_size(instrument)

    def slippage_cost_price(self, instrument: str) -> float:
        return self.slippage_pips * pip_size(instrument)

    def entry_cost_price(self, instrument: str) -> float:
        """Coste total (spread + slippage) aplicado en cada lado de la
        operacion, expresado en unidades de precio del instrumento."""
        return self.spread_cost_price(instrument) + self.slippage_cost_price(instrument)

    def commission_eur(self, lots: float) -> float:
        return self.commission_per_lot_round_turn_eur * lots

    def swap_eur(self, instrument: str, direction: str, nights: int, lots: float) -> float:
        if nights <= 0:
            return 0.0
        table = self.swap_overrides_long if direction == "long" else self.swap_overrides_short
        per_night = table.get(instrument, self.default_swap_per_lot_per_night_eur)
        return per_night * lots * nights
