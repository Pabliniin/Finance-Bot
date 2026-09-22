"""Modelo de probabilidad: P(TP1 antes que stop) y P(TP2 antes que stop)
para cada candidato, a partir de la confluencia de estrategias y del contexto.

- Regresion logistica regularizada (L2): pocas hipotesis, coeficientes
  interpretables ("cuanto aporta de verdad cada estrategia") y dificil de
  sobreajustar comparada con modelos mas flexibles.
- Entrenamiento walk-forward con PURGA: para predecir una ventana solo se usan
  candidatos cuyo resultado ya se conocia antes de que empezara (una
  operacion que termina dentro de la ventana de test usaria precios futuros).
- Calibracion de Platt (2 parametros, monotona) ajustada SOLO con
  predicciones fuera de muestra del periodo de validacion. NO isotonica: con
  pocos casos en la cola, la isotonica "aprende" escalones a medida de un
  puñado de aciertos (p.ej. 94% con 18 casos) y el umbral elegido despues
  sobre esa misma validacion selecciona justo ese ruido.
- Se guarda como JSON (coeficientes y tablas), nunca con pickle: un fichero
  de modelo manipulado no puede ejecutar codigo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

REGULARIZATION_C = 0.3


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def platt(p_raw: np.ndarray, a: float, b: float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-(a * _logit(np.asarray(p_raw, dtype="float64")) + b)))


@dataclass
class LinearHead:
    coef: list[float]
    intercept: float
    calib_a: float | None = None
    calib_b: float | None = None

    def raw(self, z: np.ndarray) -> np.ndarray:
        logits = z @ np.asarray(self.coef) + self.intercept
        return 1.0 / (1.0 + np.exp(-logits))

    def calibrated(self, p_raw: np.ndarray) -> np.ndarray:
        if self.calib_a is None or self.calib_b is None:
            return p_raw
        return platt(p_raw, self.calib_a, self.calib_b)


@dataclass
class ProbabilityModel:
    feature_names: list[str]
    mean: list[float]
    scale: list[float]
    tp1: LinearHead
    tp2: LinearHead
    metadata: dict = field(default_factory=dict)

    def _standardize(self, X: pd.DataFrame) -> np.ndarray:
        missing = set(self.feature_names) - set(X.columns)
        if missing:
            raise ValueError(f"faltan features para el modelo: {sorted(missing)}")
        z = X[self.feature_names].to_numpy(dtype="float64")
        return (z - np.asarray(self.mean)) / np.asarray(self.scale)

    def predict(self, X: pd.DataFrame, calibrated: bool = True) -> pd.DataFrame:
        z = self._standardize(X)
        p1, p2 = self.tp1.raw(z), self.tp2.raw(z)
        if calibrated:
            p1, p2 = self.tp1.calibrated(p1), self.tp2.calibrated(p2)
        p2 = np.minimum(p2, p1)  # llegar a 2R implica haber pasado por 1R
        return pd.DataFrame({"p_tp1": p1, "p_tp2": p2}, index=X.index)

    def contributions(self, x_row: pd.Series) -> pd.Series:
        """Aporte de cada feature al logit de TP1 para UNA fila (explicabilidad:
        que estrategias empujan la probabilidad arriba o abajo en esta señal)."""
        z = self._standardize(x_row.to_frame().T)[0]
        return pd.Series(z * np.asarray(self.tp1.coef), index=self.feature_names)

    def to_json(self, path: Path) -> None:
        payload = {
            "feature_names": self.feature_names,
            "mean": self.mean,
            "scale": self.scale,
            "tp1": self.tp1.__dict__,
            "tp2": self.tp2.__dict__,
            "metadata": self.metadata,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1, default=float), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def from_json(cls, path: Path) -> ProbabilityModel:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            feature_names=payload["feature_names"],
            mean=payload["mean"],
            scale=payload["scale"],
            tp1=LinearHead(**payload["tp1"]),
            tp2=LinearHead(**payload["tp2"]),
            metadata=payload.get("metadata", {}),
        )


def fit(X: pd.DataFrame, y_tp1: pd.Series, y_tp2: pd.Series, c: float = REGULARIZATION_C) -> ProbabilityModel:
    mean = X.mean().to_numpy()
    scale = X.std(ddof=0).replace(0, 1.0).to_numpy()
    z = (X.to_numpy(dtype="float64") - mean) / scale
    heads = []
    for y in (y_tp1, y_tp2):
        clf = LogisticRegression(C=c, max_iter=3000)
        clf.fit(z, y.to_numpy().astype(int))
        heads.append(LinearHead(coef=clf.coef_[0].tolist(), intercept=float(clf.intercept_[0])))
    return ProbabilityModel(
        feature_names=list(X.columns),
        mean=mean.tolist(),
        scale=scale.tolist(),
        tp1=heads[0],
        tp2=heads[1],
    )


def fit_calibration(p_raw: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Platt: y ~ sigmoid(a * logit(p_raw) + b). Devuelve (a, b)."""
    clf = LogisticRegression(C=1e6, max_iter=1000)
    clf.fit(_logit(np.asarray(p_raw, dtype="float64")).reshape(-1, 1), np.asarray(y).astype(int))
    return float(clf.coef_[0][0]), float(clf.intercept_[0])


def walk_forward(
    dataset: pd.DataFrame,
    feature_names: list[str],
    first_test: pd.Timestamp,
    step_months: int,
    min_train: int = 2000,
    min_train_per_tf: int = 300,
) -> pd.DataFrame:
    """Predicciones fuera de muestra (sin calibrar) para cada candidato con
    close_time >= first_test. `dataset` necesita: close_time, exit_time, tf,
    hit_tp1, hit_tp2 y las features. Devuelve p_tp1_raw, p_tp2_raw y el
    instante de corte del modelo que hizo cada prediccion."""
    data = dataset.sort_values("close_time")
    end = data["close_time"].max()
    preds = []
    window_start = first_test
    while window_start <= end:
        window_end = window_start + pd.DateOffset(months=step_months)
        train = data[data["exit_time"] < window_start]  # purga: resultado conocido antes del corte
        test = data[(data["close_time"] >= window_start) & (data["close_time"] < window_end)]
        if len(train) >= min_train and not test.empty:
            tf_counts = train["tf"].value_counts()
            eligible = test[test["tf"].map(tf_counts).fillna(0) >= min_train_per_tf]
            if not eligible.empty:
                model = fit(train[feature_names], train["hit_tp1"], train["hit_tp2"])
                p = model.predict(eligible[feature_names], calibrated=False)
                p.columns = ["p_tp1_raw", "p_tp2_raw"]
                p["model_cutoff"] = window_start
                preds.append(p)
        window_start = window_end
    return pd.concat(preds) if preds else pd.DataFrame(columns=["p_tp1_raw", "p_tp2_raw", "model_cutoff"])
