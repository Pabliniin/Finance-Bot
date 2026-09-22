from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from finance_bot.engine import model as model_module
from finance_bot.engine.model import ProbabilityModel, fit, fit_calibration, platt, walk_forward


def _dataset(n: int = 3000, seed: int = 0) -> tuple[pd.DataFrame, list[str]]:
    rng = np.random.default_rng(seed)
    close_time = pd.date_range("2012-01-01", periods=n, freq="36h", tz="UTC")
    x1, x2 = rng.normal(size=n), rng.normal(size=n)
    p = 1 / (1 + np.exp(-(0.8 * x1 - 0.3)))
    y1 = (rng.uniform(size=n) < p).astype(float)
    y2 = y1 * (rng.uniform(size=n) < 0.5)
    df = pd.DataFrame(
        {
            "close_time": close_time,
            "exit_time": close_time + pd.Timedelta(days=5),
            "tf": "H4",
            "hit_tp1": y1,
            "hit_tp2": y2,
            "f1": x1,
            "f2": x2,
        },
        index=[f"id{i}" for i in range(n)],
    )
    return df, ["f1", "f2"]


def test_walk_forward_purges_trades_that_end_after_the_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    data, features = _dataset()
    seen_cuts: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    real_fit = model_module.fit

    def spying_fit(X, y1, y2, c=0.3):
        seen_cuts.append(X.index.map(data["exit_time"]).max())
        return real_fit(X, y1, y2, c)

    monkeypatch.setattr(model_module, "fit", spying_fit)
    preds = walk_forward(
        data, features, pd.Timestamp("2015-01-01", tz="UTC"), step_months=6, min_train=200, min_train_per_tf=50
    )
    assert not preds.empty
    for cutoff, rows in preds.groupby("model_cutoff"):
        assert (data.loc[rows.index, "close_time"] >= cutoff).all()  # solo predice el futuro del corte
    # ningun modelo entreno con operaciones cuyo resultado se conocia DESPUES de su corte
    cutoffs = sorted(preds["model_cutoff"].unique())
    assert all(max_exit < cut for max_exit, cut in zip(seen_cuts, cutoffs, strict=True))


def test_model_learns_signal_and_json_roundtrip(tmp_path) -> None:
    data, features = _dataset()
    m = fit(data[features], data["hit_tp1"], data["hit_tp2"])
    p = m.predict(data[features], calibrated=False)
    assert np.corrcoef(p["p_tp1"], data["f1"])[0, 1] > 0.9  # aprende que f1 sube la probabilidad
    assert (p["p_tp2"] <= p["p_tp1"] + 1e-12).all()

    path = tmp_path / "model.json"
    m.tp1.calib_a, m.tp1.calib_b = 0.9, 0.1
    m.to_json(path)
    loaded = ProbabilityModel.from_json(path)
    pd.testing.assert_frame_equal(m.predict(data[features]), loaded.predict(data[features]))


def test_platt_calibration_fixes_overconfidence() -> None:
    rng = np.random.default_rng(1)
    true_p = rng.uniform(0.3, 0.6, size=20000)
    y = (rng.uniform(size=true_p.size) < true_p).astype(int)
    overconfident = 1 / (1 + np.exp(-3 * np.log(true_p / (1 - true_p))))  # exagera la confianza
    a, b = fit_calibration(overconfident, y)
    calibrated = platt(overconfident, a, b)
    assert np.abs(calibrated - true_p).mean() < np.abs(overconfident - true_p).mean() / 3
    assert np.all(np.diff(platt(np.linspace(0.01, 0.99, 50), a, b)) > 0)  # monotona


def test_missing_feature_is_an_error() -> None:
    data, features = _dataset(500)
    m = fit(data[features], data["hit_tp1"], data["hit_tp2"])
    with pytest.raises(ValueError, match="faltan features"):
        m.predict(data[["f1"]])
