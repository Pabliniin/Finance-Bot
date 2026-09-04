"""Persistencia simple (JSON en disco, volumen compartido `reports_data` de
docker-compose) del ultimo informe de backtest por estrategia. Es lo que lee
`/backtest` en Discord. Deliberadamente NO es una tabla de Postgres: es un
informe de una ejecucion offline (scripts/run_full_backtest.py, Fase 3), no
estado transaccional que cambie con cada operacion - un fichero versionable
es mas simple y mas facil de inspeccionar a mano.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

_DEFAULT_PATH = Path("reports") / "backtest_summary.json"


def save_backtest_report(report: dict, path: Path | str = _DEFAULT_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    report = dict(report)
    report["generated_at"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def load_backtest_report(path: Path | str = _DEFAULT_PATH) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
