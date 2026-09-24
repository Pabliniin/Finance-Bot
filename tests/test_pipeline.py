"""Comprobaciones del pipeline de investigacion que no necesitan datos ni red."""

from __future__ import annotations

from finance_bot.research.pipeline import research_timeframes


def test_research_excludes_m1(cfg) -> None:
    """M1 no se entrena ni valida (usa M15 como proxy en vivo): incluirlo metia
    cientos de miles de candidatos mal etiquetados y contaminaba el modelo. El
    resto de temporalidades si se entrenan."""
    assert "M1" in cfg.timeframes  # esta configurado para señales en vivo
    tfs = research_timeframes(cfg)
    assert "M1" not in tfs
    assert {"M15", "H1", "H4", "D1"} <= set(tfs)
