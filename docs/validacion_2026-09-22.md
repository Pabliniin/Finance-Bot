# Informe de validación — 2026-09-22 13:09 UTC

Todo es **fuera de muestra** (cada probabilidad la calculó un modelo que no había visto ese periodo), con costes (spread, deslizamiento, swap). R = lo que arriesgas por operación.

- Test reservado evaluado: **SÍ — ejecución final única**
- Combinaciones plan × grupo contrastadas (corrección BH global, α=0.05): 10

## 1. Datos

| Instrumento | Temporalidad | Velas | Desde | Hasta |
|---|---|---:|---|---|
| XAUUSD | M15 | 135,239 | 2021-01-03 | 2026-09-21 |
| XAUUSD | H1 | 87,738 | 2012-01-01 | 2026-09-21 |
| XAUUSD | H4 | 22,771 | 2012-01-01 | 2026-09-21 |
| XAUUSD | D1 | 3,804 | 2012-01-01 | 2026-09-20 |
| EURUSD | M15 | 142,677 | 2021-01-03 | 2026-09-21 |
| EURUSD | H1 | 91,787 | 2012-01-01 | 2026-09-21 |
| EURUSD | H4 | 22,955 | 2012-01-01 | 2026-09-21 |
| EURUSD | D1 | 3,834 | 2012-01-01 | 2026-09-20 |

## 2. Resumen por plan (validación)

| Plan | TP1 / TP2 | Acierto TP1 base | AUC | Umbral | Señales | Acierto TP1 | Expectativa R/op | t |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| equilibrado | 1.0R / 2.0R | 38.2% | 0.573 | 52.5% | 461 | 51.4% | +0.014 | 0.29 |
| alto_acierto | 0.5R / 1.0R | 57.0% | 0.550 | 65.0% | 833 | 63.1% | -0.020 | -0.77 |
| tendencia | 1.5R / 3.0R | 31.7% | 0.558 | 42.5% | 238 | 41.6% | +0.044 | 0.50 |

Para no perder dinero con un plan, el acierto debe superar el de equilibrio: con TP a X·R y stop a 1R hace falta acertar más de 1/(1+X) antes de costes (0.5R → 66.7%, 1R → 50%, 1.5R → 40%).

## Plan «equilibrado» — TP1 1.0R, TP2 2.0R, cierre por tiempo ×1.0

- Candidatos con resultado: 104,068; con predicción fuera de muestra: 89,512 (validation: 47,943, test: 41,559, gap: 10)
- AUC TP1 0.573 · Brier 0.2320 vs 0.2360 sin modelo · probabilidad TP1 calibrada p10/p50/p90/p99: 29.9% / 38.2% / 46.4% / 53.2%

Calibración en validación (probabilidad del walk-forward SIN recalibrar; la recalibración de Platt se ajusta con estos datos y su examen real es el test):

| Probabilidad | Casos | Predicho medio | Ocurrido |
|---|---:|---:|---:|
| (-0.001, 0.3] | 5,312 | 26.9% | 22.6% |
| (0.3, 0.35] | 9,396 | 32.7% | 34.1% |
| (0.35, 0.4] | 13,075 | 37.6% | 39.5% |
| (0.4, 0.45] | 11,570 | 42.3% | 42.3% |
| (0.45, 0.5] | 6,088 | 47.1% | 43.8% |
| (0.5, 0.55] | 2,043 | 51.9% | 45.5% |
| (0.55, 0.6] | 397 | 56.7% | 49.1% |
| (0.6, 0.65] | 60 | 61.7% | 65.0% |
| (0.65, 0.7] | 2 | 65.5% | 100.0% |

Estrategias de entrada por sí solas (validación, sin filtrar por el modelo):

| Estrategia | Señales | Acierto TP1 | Expectativa R/op | p vs 0 |
|---|---:|---:|---:|---:|
| Salida de compresion | 3,679 | 41.8% | -0.027 | 0.953 |
| Giro de Supertrend | 2,129 | 38.2% | -0.075 | 1.000 |
| Ruptura Donchian 20 con ADX | 10,587 | 36.0% | -0.075 | 1.000 |
| Ruptura de estructura | 6,691 | 38.5% | -0.081 | 1.000 |
| Cruce de MACD a favor de tendencia | 6,101 | 38.1% | -0.081 | 1.000 |
| Pullback a la EMA20 en tendencia | 28,599 | 38.7% | -0.095 | 1.000 |
| Reversion en Bollinger (rango) | 2,069 | 41.1% | -0.139 | 1.000 |
| Pullback de RSI (40/60) | 3,452 | 39.3% | -0.145 | 1.000 |

Umbral de probabilidad (validación):

| Umbral | Señales | Acierto TP1 | Expectativa R/op | t | Señales/año |
|---:|---:|---:|---:|---:|---:|
| 30.0% | 11500 | 40.8% | -0.092 | -9.86 | 1279 |
| 32.5% | 10848 | 41.3% | -0.091 | -9.41 | 1207 |
| 35.0% | 9875 | 41.9% | -0.091 | -8.95 | 1098 |
| 37.5% | 8639 | 42.7% | -0.090 | -8.20 | 961 |
| 40.0% | 7145 | 43.1% | -0.099 | -8.13 | 795 |
| 42.5% | 5417 | 43.7% | -0.099 | -7.06 | 603 |
| 45.0% | 3653 | 44.8% | -0.091 | -5.26 | 407 |
| 47.5% | 2166 | 46.2% | -0.070 | -3.09 | 241 |
| 50.0% | 1080 | 47.3% | -0.070 | -2.21 | 121 |
| 52.5% ◀ | 461 | 51.4% | +0.014 | 0.29 | 67 |
| 55.0% | 169 | 50.3% | +0.007 | 0.08 | 25 |
| 57.5% | 69 | 60.9% | +0.247 | 1.88 | 16 |
| 60.0% | 15 | 93.3% | +0.994 | 5.22 | 6 |
| 62.5% | 4 | 75.0% | +0.617 | 1.04 | 2 |

Grupos en validación con el umbral elegido:

| Grupo | Señales | Acierto TP1 [IC95] | Acierto TP2 | Expectativa R/op | p vs 0 | p vs azar | R/op sin filtrar | BH | Apto |
|---|---:|---|---:|---:|---:|---:|---:|---|---|
| EURUSD D1 | 0 | — | — | — | — | — | +0.042 | no | ❌ |
| EURUSD H1 | 143 | 46.2% [38.2%–54.3%] | 26.6% | -0.085 | 0.831 | 0.213 | -0.067 | no | ❌ |
| EURUSD H4 | 8 | 62.5% [30.6%–86.3%] | 12.5% | +0.057 | 0.435 | 0.323 | -0.025 | no | ❌ |
| EURUSD M15 | 0 | — | — | — | — | — | -0.155 | no | ❌ |
| XAUUSD D1 | 0 | — | — | — | — | — | -0.097 | no | ❌ |
| XAUUSD H1 | 277 | 55.2% [49.3%–61.0%] | 32.1% | +0.093 | 0.073 | <0.001 | -0.069 | no | ❌ |
| XAUUSD H4 | 33 | 39.4% [24.7%–56.3%] | 24.2% | -0.228 | 0.890 | 0.797 | -0.018 | no | ❌ |
| XAUUSD M15 | 0 | — | — | — | — | — | -0.119 | no | ❌ |

**Test reservado** — AUC 0.577 · Brier 0.2330 vs 0.2372

| Grupo | Señales | Acierto TP1 [IC95] | Acierto TP2 | Expectativa R/op | p vs 0 | p vs azar | R/op sin filtrar |
|---|---:|---|---:|---:|---:|---:|---:|
| EURUSD D1 | 0 | — | — | — | — | — | -0.083 |
| EURUSD H1 | 27 | 48.1% [30.7%–66.0%] | 25.9% | -0.046 | 0.587 | 0.370 | -0.131 |
| EURUSD H4 | 0 | — | — | — | — | — | -0.068 |
| EURUSD M15 | 0 | — | — | — | — | — | -0.171 |
| XAUUSD D1 | 0 | — | — | — | — | — | -0.017 |
| XAUUSD H1 | 119 | 40.3% [32.0%–49.3%] | 27.7% | -0.185 | 0.973 | 0.680 | -0.054 |
| XAUUSD H4 | 0 | — | — | — | — | — | +0.007 |
| XAUUSD M15 | 1 | 100.0% [20.7%–100.0%] | 0.0% | +0.492 | — | 0.260 | -0.069 |

Calibración en test (aquí sí con la recalibración, que no vio estos datos):

| Probabilidad | Casos | Predicho medio | Ocurrido |
|---|---:|---:|---:|
| (-0.001, 0.3] | 5,954 | 26.9% | 24.1% |
| (0.3, 0.35] | 8,366 | 32.7% | 35.6% |
| (0.35, 0.4] | 11,733 | 37.6% | 40.9% |
| (0.4, 0.45] | 10,469 | 42.3% | 43.5% |
| (0.45, 0.5] | 4,225 | 47.0% | 46.0% |
| (0.5, 0.55] | 792 | 51.5% | 45.3% |
| (0.55, 0.6] | 20 | 55.7% | 30.0% |

## Plan «alto_acierto» — TP1 0.5R, TP2 1.0R, cierre por tiempo ×1.0

- Candidatos con resultado: 104,069; con predicción fuera de muestra: 89,513 (validation: 47,944, test: 41,560, gap: 9)
- AUC TP1 0.550 · Brier 0.2431 vs 0.2451 sin modelo · probabilidad TP1 calibrada p10/p50/p90/p99: 51.0% / 57.3% / 62.5% / 66.3%

Calibración en validación (probabilidad del walk-forward SIN recalibrar; la recalibración de Platt se ajusta con estos datos y su examen real es el test):

| Probabilidad | Casos | Predicho medio | Ocurrido |
|---|---:|---:|---:|
| (0.35, 0.4] | 41 | 38.8% | 39.0% |
| (0.4, 0.45] | 694 | 43.4% | 38.8% |
| (0.45, 0.5] | 3,985 | 48.0% | 47.9% |
| (0.5, 0.55] | 10,475 | 52.8% | 54.0% |
| (0.55, 0.6] | 17,475 | 57.5% | 57.8% |
| (0.6, 0.65] | 12,558 | 62.2% | 61.3% |
| (0.65, 0.7] | 2,583 | 66.6% | 62.3% |
| (0.7, 1.0] | 133 | 71.3% | 60.9% |

Estrategias de entrada por sí solas (validación, sin filtrar por el modelo):

| Estrategia | Señales | Acierto TP1 | Expectativa R/op | p vs 0 |
|---|---:|---:|---:|---:|
| Salida de compresion | 3,679 | 59.6% | -0.035 | 0.998 |
| Giro de Supertrend | 2,129 | 58.5% | -0.069 | 1.000 |
| Ruptura Donchian 20 con ADX | 10,587 | 56.3% | -0.078 | 1.000 |
| Cruce de MACD a favor de tendencia | 6,101 | 57.6% | -0.080 | 1.000 |
| Ruptura de estructura | 6,691 | 57.3% | -0.082 | 1.000 |
| Pullback a la EMA20 en tendencia | 28,599 | 57.1% | -0.096 | 1.000 |
| Pullback de RSI (40/60) | 3,453 | 57.5% | -0.123 | 1.000 |
| Reversion en Bollinger (rango) | 2,070 | 57.3% | -0.131 | 1.000 |

Umbral de probabilidad (validación):

| Umbral | Señales | Acierto TP1 | Expectativa R/op | t | Señales/año |
|---:|---:|---:|---:|---:|---:|
| 30.0% | 17864 | 58.4% | -0.087 | -15.71 | 1987 |
| 32.5% | 17864 | 58.4% | -0.087 | -15.71 | 1987 |
| 35.0% | 17864 | 58.4% | -0.087 | -15.71 | 1987 |
| 37.5% | 17864 | 58.4% | -0.087 | -15.71 | 1987 |
| 40.0% | 17864 | 58.4% | -0.087 | -15.71 | 1987 |
| 42.5% | 17859 | 58.4% | -0.087 | -15.71 | 1986 |
| 45.0% | 17839 | 58.4% | -0.087 | -15.78 | 1984 |
| 47.5% | 17700 | 58.6% | -0.086 | -15.54 | 1969 |
| 50.0% | 17312 | 58.8% | -0.086 | -15.32 | 1925 |
| 52.5% | 16356 | 59.2% | -0.084 | -14.49 | 1819 |
| 55.0% | 14407 | 59.7% | -0.081 | -13.04 | 1602 |
| 57.5% | 10922 | 60.3% | -0.075 | -10.47 | 1215 |
| 60.0% | 6755 | 61.8% | -0.051 | -5.58 | 752 |
| 62.5% | 2973 | 61.5% | -0.050 | -3.67 | 331 |
| 65.0% ◀ | 833 | 63.1% | -0.020 | -0.77 | 93 |
| 67.5% | 159 | 56.0% | -0.123 | -2.06 | 23 |
| 70.0% | 23 | 52.2% | -0.157 | -0.97 | 6 |

Grupos en validación con el umbral elegido:

| Grupo | Señales | Acierto TP1 [IC95] | Acierto TP2 | Expectativa R/op | p vs 0 | p vs azar | R/op sin filtrar | BH | Apto |
|---|---:|---|---:|---:|---:|---:|---:|---|---|
| EURUSD D1 | 0 | — | — | — | — | — | +0.004 | no | ❌ |
| EURUSD H1 | 206 | 61.7% [54.9%–68.0%] | 44.7% | -0.062 | 0.879 | 0.073 | -0.064 | no | ❌ |
| EURUSD H4 | 60 | 66.7% [54.1%–77.3%] | 43.3% | +0.082 | 0.195 | 0.020 | -0.064 | no | ❌ |
| EURUSD M15 | 1 | 0.0% [0.0%–79.3%] | 0.0% | -1.009 | — | 0.700 | -0.139 | no | ❌ |
| XAUUSD D1 | 0 | — | — | — | — | — | -0.046 | no | ❌ |
| XAUUSD H1 | 393 | 65.1% [60.3%–69.7%] | 51.4% | +0.017 | 0.329 | <0.001 | -0.072 | no | ❌ |
| XAUUSD H4 | 167 | 59.3% [51.7%–66.4%] | 40.1% | -0.090 | 0.942 | 0.587 | -0.038 | no | ❌ |
| XAUUSD M15 | 6 | 66.7% [30.0%–90.3%] | 66.7% | +0.076 | 0.419 | 0.207 | -0.093 | no | ❌ |

**Test reservado** — AUC 0.552 · Brier 0.2428 vs 0.2449

| Grupo | Señales | Acierto TP1 [IC95] | Acierto TP2 | Expectativa R/op | p vs 0 | p vs azar | R/op sin filtrar |
|---|---:|---|---:|---:|---:|---:|---:|
| EURUSD D1 | 0 | — | — | — | — | — | -0.064 |
| EURUSD H1 | 32 | 78.1% [61.2%–89.0%] | 65.6% | +0.143 | 0.107 | 0.010 | -0.099 |
| EURUSD H4 | 16 | 62.5% [38.6%–81.5%] | 43.8% | -0.029 | 0.562 | 0.600 | -0.062 |
| EURUSD M15 | 1 | 100.0% [20.7%–100.0%] | 0.0% | +0.239 | — | 0.403 | -0.182 |
| XAUUSD D1 | 0 | — | — | — | — | — | +0.016 |
| XAUUSD H1 | 93 | 60.2% [50.1%–69.6%] | 39.8% | -0.046 | 0.725 | 0.200 | -0.050 |
| XAUUSD H4 | 87 | 70.1% [59.8%–78.7%] | 47.1% | +0.019 | 0.396 | 0.137 | -0.011 |
| XAUUSD M15 | 8 | 62.5% [30.6%–86.3%] | 37.5% | -0.163 | 0.728 | 0.543 | -0.064 |

Calibración en test (aquí sí con la recalibración, que no vio estos datos):

| Probabilidad | Casos | Predicho medio | Ocurrido |
|---|---:|---:|---:|
| (0.4, 0.45] | 248 | 44.0% | 41.1% |
| (0.45, 0.5] | 3,883 | 48.1% | 46.3% |
| (0.5, 0.55] | 10,080 | 53.0% | 54.4% |
| (0.55, 0.6] | 18,400 | 57.5% | 58.5% |
| (0.6, 0.65] | 8,644 | 61.7% | 62.4% |
| (0.65, 0.7] | 304 | 65.9% | 64.1% |
| (0.7, 1.0] | 1 | 71.0% | 100.0% |

## Plan «tendencia» — TP1 1.5R, TP2 3.0R, cierre por tiempo ×2.0

- Candidatos con resultado: 104,065; con predicción fuera de muestra: 89,508 (validation: 47,931, test: 41,554, gap: 23)
- AUC TP1 0.558 · Brier 0.2148 vs 0.2167 sin modelo · probabilidad TP1 calibrada p10/p50/p90/p99: 26.0% / 31.6% / 37.5% / 42.3%

Calibración en validación (probabilidad del walk-forward SIN recalibrar; la recalibración de Platt se ajusta con estos datos y su examen real es el test):

| Probabilidad | Casos | Predicho medio | Ocurrido |
|---|---:|---:|---:|
| (-0.001, 0.3] | 16,629 | 26.4% | 26.3% |
| (0.3, 0.35] | 14,959 | 32.5% | 33.1% |
| (0.35, 0.4] | 11,183 | 37.2% | 35.9% |
| (0.4, 0.45] | 4,259 | 41.9% | 35.6% |
| (0.45, 0.5] | 792 | 46.8% | 39.1% |
| (0.5, 0.55] | 105 | 51.5% | 42.9% |
| (0.55, 0.6] | 4 | 56.1% | 75.0% |

Estrategias de entrada por sí solas (validación, sin filtrar por el modelo):

| Estrategia | Señales | Acierto TP1 | Expectativa R/op | p vs 0 |
|---|---:|---:|---:|---:|
| Salida de compresion | 3,676 | 35.0% | -0.029 | 0.919 |
| Giro de Supertrend | 2,129 | 31.8% | -0.080 | 0.999 |
| Cruce de MACD a favor de tendencia | 6,098 | 31.8% | -0.085 | 1.000 |
| Ruptura Donchian 20 con ADX | 10,583 | 30.0% | -0.089 | 1.000 |
| Ruptura de estructura | 6,686 | 31.6% | -0.091 | 1.000 |
| Pullback a la EMA20 en tendencia | 28,591 | 32.2% | -0.108 | 1.000 |
| Reversion en Bollinger (rango) | 2,069 | 34.5% | -0.118 | 1.000 |
| Pullback de RSI (40/60) | 3,452 | 31.6% | -0.167 | 1.000 |

Umbral de probabilidad (validación):

| Umbral | Señales | Acierto TP1 | Expectativa R/op | t | Señales/año |
|---:|---:|---:|---:|---:|---:|
| 30.0% | 6856 | 34.3% | -0.105 | -7.01 | 763 |
| 32.5% | 5142 | 35.3% | -0.098 | -5.62 | 572 |
| 35.0% | 3484 | 36.9% | -0.079 | -3.64 | 388 |
| 37.5% | 1926 | 37.3% | -0.091 | -3.16 | 214 |
| 40.0% | 758 | 36.9% | -0.099 | -2.14 | 101 |
| 42.5% ◀ | 238 | 41.6% | +0.044 | 0.50 | 34 |
| 45.0% | 74 | 44.6% | +0.099 | 0.63 | 17 |
| 47.5% | 13 | 46.2% | +0.139 | 0.36 | 6 |
| 50.0% | 1 | 0.0% | -1.006 | — | 12 |

Grupos en validación con el umbral elegido:

| Grupo | Señales | Acierto TP1 [IC95] | Acierto TP2 | Expectativa R/op | p vs 0 | p vs azar | R/op sin filtrar | BH | Apto |
|---|---:|---|---:|---:|---:|---:|---:|---|---|
| EURUSD D1 | 0 | — | — | — | — | — | -0.002 | no | ❌ |
| EURUSD H1 | 68 | 38.2% [27.6%–50.1%] | 16.2% | -0.120 | 0.787 | 0.393 | -0.063 | no | ❌ |
| EURUSD H4 | 14 | 35.7% [16.3%–61.2%] | 21.4% | -0.167 | 0.687 | 0.560 | -0.033 | no | ❌ |
| EURUSD M15 | 0 | — | — | — | — | — | -0.169 | no | ❌ |
| XAUUSD D1 | 0 | — | — | — | — | — | -0.160 | no | ❌ |
| XAUUSD H1 | 122 | 46.7% [38.1%–55.5%] | 30.3% | +0.204 | 0.054 | <0.001 | -0.061 | no | ❌ |
| XAUUSD H4 | 34 | 32.4% [19.1%–49.2%] | 23.5% | -0.116 | 0.685 | 0.517 | -0.014 | no | ❌ |
| XAUUSD M15 | 0 | — | — | — | — | — | -0.112 | no | ❌ |

**Test reservado** — AUC 0.570 · Brier 0.2186 vs 0.2212

| Grupo | Señales | Acierto TP1 [IC95] | Acierto TP2 | Expectativa R/op | p vs 0 | p vs azar | R/op sin filtrar |
|---|---:|---|---:|---:|---:|---:|---:|
| EURUSD D1 | 0 | — | — | — | — | — | -0.060 |
| EURUSD H1 | 10 | 30.0% [10.8%–60.3%] | 30.0% | -0.186 | 0.658 | 0.527 | -0.116 |
| EURUSD H4 | 0 | — | — | — | — | — | -0.032 |
| EURUSD M15 | 0 | — | — | — | — | — | -0.166 |
| XAUUSD D1 | 0 | — | — | — | — | — | +0.025 |
| XAUUSD H1 | 73 | 39.7% [29.3%–51.2%] | 26.0% | -0.019 | 0.549 | 0.213 | -0.029 |
| XAUUSD H4 | 3 | 33.3% [6.1%–79.2%] | 0.0% | -0.218 | 0.596 | 0.563 | +0.072 |
| XAUUSD M15 | 0 | — | — | — | — | — | -0.076 |

Calibración en test (aquí sí con la recalibración, que no vio estos datos):

| Probabilidad | Casos | Predicho medio | Ocurrido |
|---|---:|---:|---:|
| (-0.001, 0.3] | 17,576 | 27.0% | 27.8% |
| (0.3, 0.35] | 16,347 | 32.4% | 35.2% |
| (0.35, 0.4] | 6,889 | 36.9% | 39.8% |
| (0.4, 0.45] | 737 | 41.3% | 41.8% |
| (0.45, 0.5] | 5 | 45.8% | 40.0% |

## Qué aporta cada factor a la confluencia (modelo final, plan «equilibrado»)

Coeficientes estandarizados del modelo de TP1: positivo = sube la probabilidad cuando está a favor. Muchas estrategias miden casi lo mismo; un coeficiente ~0 significa que no añade nada que no digan ya otras.

| Factor | Coeficiente |
|---|---:|
| Sesion de Nueva York | -0.145 |
| Stop ancho (en ATR) | -0.125 |
| Sesion de Londres | +0.110 |
| Temporalidad D1 | -0.087 |
| Posicion en Bollinger a favor | +0.079 |
| RSI a favor | +0.075 |
| Temporalidad H1 | +0.055 |
| Temporalidad M15 | -0.053 |
| RSI de la temporalidad superior a favor | -0.052 |
| Alineacion EMA 20/50/200 | +0.048 |
| Volatilidad relativa | -0.042 |
| Temporalidad H4 | +0.040 |
| Espacio hasta el siguiente nivel | -0.036 |
| Nube de Ichimoku | -0.034 |
| Instrumento EURUSD | -0.033 |
| Instrumento XAUUSD | +0.033 |
| Salida de compresion (squeeze) | +0.031 |
| Fuerza de tendencia (ADX) | -0.028 |
| Distancia a la EMA200 a favor | +0.025 |
| Pendiente de la EMA200 a favor | +0.025 |
| Tendencia de la temporalidad superior | +0.025 |
| Impulso reciente a favor | -0.024 |
| MACD (12, 26, 9) | +0.022 |
| Regimen de RSI (14) | -0.021 |
| Soportes y resistencias | -0.020 |

## Conclusión

**Ninguna combinación de plan, instrumento y temporalidad supera la validación con corrección por comparaciones múltiples.** En modo estricto el bot no emitirá señales de entrada: mostrará el análisis completo y la probabilidad estimada, pero no te dirá que entres. Es el resultado honesto.

Sin combinaciones seleccionadas, no hay nada que evaluar en el test.

> Estimacion estadistica basada en historico fuera de muestra, no una garantia. No es asesoramiento financiero. Rendimientos pasados no garantizan resultados futuros. Tu decides y ejecutas; el bot nunca opera por ti.