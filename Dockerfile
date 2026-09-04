FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Capa de dependencias separada del codigo para aprovechar la cache de Docker:
# solo se reinstalan paquetes si pyproject.toml cambia, no en cada edicion de codigo.
COPY pyproject.toml ./
RUN uv pip install --system --no-cache -e .

COPY . .

# Nota: las extras "windows-collector" (MetaTrader5, duka) NUNCA se instalan aqui.
# Esta imagen es Linux y sirve al bot de Discord + scheduler + backtest, que solo
# leen/escriben en Postgres. El collector de MT5 corre nativo en el mini PC Windows.

RUN useradd --create-home --uid 1000 botuser
USER botuser

CMD ["python", "-m", "bot.main"]
