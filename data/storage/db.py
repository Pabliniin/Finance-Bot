"""Engine/conexion SQLAlchemy compartido. Un unico punto de creacion para que
tanto los servicios Docker (DATABASE_URL apuntando a 'db:5432') como el
collector nativo de Windows (DATABASE_URL_HOST apuntando a 'localhost:5432',
puerto publicado por docker-compose) puedan reusar exactamente el mismo codigo
de lectura/escritura de data/storage/cache.py.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


@lru_cache
def get_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True, future=True)


def get_session(database_url: str) -> Session:
    engine = get_engine(database_url)
    factory = sessionmaker(bind=engine, future=True)
    return factory()
