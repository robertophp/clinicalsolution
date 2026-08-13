"""
Usuarios del dashboard en Firestore (colección ``dashboard_users``).

Documento (id = username en minúsculas):
- ``username``: str
- ``password_hash``: str (ver dashboard_security.hash_password)
- ``clinic_id``: str (clínica a la que pertenece; define qué datos puede ver)
- ``active``: bool

Hasta 3 usuarios por clínica, todos solo-lectura con los mismos permisos.
"""
from __future__ import annotations

import logging

from google.cloud import firestore

from ..config import settings
from ..domain.runtime_env import effective_firestore_database_id
from .dashboard_security import verify_password

logger = logging.getLogger(__name__)

DASHBOARD_USERS_COLLECTION = "dashboard_users"


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


class DashboardUserRepository:
    """Lee y autentica usuarios del dashboard desde Firestore."""

    def __init__(self, project_id: str | None = None) -> None:
        self._project_id = project_id or settings.PROJECT_ID
        self._client: firestore.Client | None = None

    @property
    def _db(self) -> firestore.Client:
        if self._client is None:
            database_id = effective_firestore_database_id(
                app_env=settings.APP_ENV,
                configured=getattr(settings, "FIRESTORE_DATABASE_ID", None),
            )
            self._client = firestore.Client(project=self._project_id, database=database_id)
        return self._client

    def get_user(self, username: str) -> dict | None:
        uname = normalize_username(username)
        if not uname:
            return None
        doc = self._db.collection(DASHBOARD_USERS_COLLECTION).document(uname).get()
        if not doc or not doc.exists:
            return None
        return doc.to_dict() or None

    def authenticate(self, username: str, password: str) -> dict | None:
        """
        Devuelve {clinic_id, username} si las credenciales son válidas y el usuario
        está activo; ``None`` en caso contrario.
        """
        user = self.get_user(username)
        if not user:
            return None
        if not user.get("active", True):
            return None
        stored_hash = user.get("password_hash") or ""
        if not verify_password(password, stored_hash):
            return None
        clinic_id = (user.get("clinic_id") or "").strip()
        if not clinic_id:
            return None
        return {"clinic_id": clinic_id, "username": normalize_username(username)}


__all__ = ["DashboardUserRepository", "DASHBOARD_USERS_COLLECTION", "normalize_username"]
