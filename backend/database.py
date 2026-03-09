from __future__ import annotations

from datetime import datetime
from typing import Generator

from sqlalchemy import Column, DateTime, Integer, String, func, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""


def _create_engine() -> Engine:
    """Create a SQLAlchemy engine for BigQuery."""
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
    """Modelo de cita para almacenar reservas de pacientes."""

    __tablename__ = "citas"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    clinic_id: str = Column(String(255), nullable=False, index=True)
    paciente_nombre: str = Column(String(255), nullable=False)
    telefono: str = Column(String(50), nullable=False)
    sintoma: str = Column(String(500), nullable=True)
    fecha_cita: datetime = Column(DateTime(timezone=False), nullable=False)
    timestamp: datetime = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


def get_db() -> Generator[Session, None, None]:
    """Yield a database session, ensuring proper cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


__all__ = ["Base", "Cita", "engine", "SessionLocal", "get_db"]

