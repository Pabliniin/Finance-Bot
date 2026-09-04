-- Estado del kill switch: es deliberadamente la UNICA pieza de estado de
-- riesgo que se persiste aparte (perdida diaria/semanal se calculan al vuelo
-- desde la tabla trades de 003_tracking.sql, no se duplican aqui). El kill
-- switch necesita persistencia propia porque es "pegajoso": una vez
-- suspendido, se queda suspendido hasta revision manual aunque el drawdown
-- se recupere solo, y eso no se puede derivar de una query.

CREATE TABLE IF NOT EXISTS risk_state (
    key           TEXT PRIMARY KEY,
    value         JSONB NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
