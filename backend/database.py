from __future__ import annotations

from datetime import datetime
from typing import Generator

from sqlalchemy import Column, Date, String, Time, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy_bigquery import TIMESTAMP

from .config import settings


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""


def _create_engine() -> Engine:
    """Create a SQLAlchemy engine for BigQuery."""
    dataset = getattr(settings, "BIGQUERY_DATASET", None)
    if dataset:
        database_url = f"bigquery://{settings.PROJECT_ID}/{dataset}"
    else:
        database_url = f"bigquery://{settings.PROJECT_ID}"
    return create_engine(database_url)


engine: Engine = _create_engine()
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=Session,
)


class Cita(Base):
    """
    Modelo de cita para almacenar reservas en BigQuery (clinica_datos.citas).
    Nombres de columna alineados con el esquema real de la tabla.
    """
    __tablename__ = "citas"

    paciente_nombre: str = Column("paciente_nombre", String(255), nullable=True)
    telefono: str = Column("telefono", String(255), nullable=True)
    fecha_cita = Column("fecha_cita", Date(), nullable=True)
    hora_cita = Column("hora_cita", Time(), nullable=True)
    clinic_id: str = Column("clinica_id", String(255), nullable=True)
    status: str = Column("status", String(64), nullable=True)
    timestamp: datetime = Column(
        "creado_en",
        TIMESTAMP(timezone=True),
        nullable=True,
        primary_key=True,
    )


def get_db() -> Generator[Session, None, None]:
    """Yield a database session, ensuring proper cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


__all__ = ["Base", "Cita", "engine", "SessionLocal", "get_db"]

