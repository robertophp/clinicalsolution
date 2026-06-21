"""
Crea (o actualiza) un usuario del dashboard en Firestore.

Uso:
    python scripts/create_dashboard_user.py --username doctora --clinic-id demo_clinic_1
    # te pedirá la contraseña de forma interactiva (no queda en el historial del shell)

O de forma no interactiva (menos seguro):
    python scripts/create_dashboard_user.py -u doctora -c demo_clinic_1 -p "ClaveFuerte#123"

Hasta 3 usuarios por clínica (todos solo-lectura, mismos permisos). El id del documento
es el username en minúsculas.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from google.cloud import firestore

from backend.config import settings
from backend.domain.runtime_env import effective_firestore_database_id
from backend.services.dashboard_security import hash_password
from backend.services.dashboard_users import DASHBOARD_USERS_COLLECTION, normalize_username


def main() -> int:
    parser = argparse.ArgumentParser(description="Crea/actualiza un usuario del dashboard en Firestore.")
    parser.add_argument("-u", "--username", required=True, help="Nombre de usuario (login).")
    parser.add_argument("-c", "--clinic-id", required=True, help="clinic_id de la clínica (ej. demo_clinic_1).")
    parser.add_argument("-p", "--password", default=None, help="Contraseña (si se omite, se pide interactiva).")
    parser.add_argument("--inactive", action="store_true", help="Crear el usuario como inactivo.")
    args = parser.parse_args()

    username = normalize_username(args.username)
    if not username:
        print("Username inválido.", file=sys.stderr)
        return 2

    password = args.password or getpass.getpass("Contraseña: ")
    if not password or len(password) < 8:
        print("La contraseña debe tener al menos 8 caracteres.", file=sys.stderr)
        return 2

    database_id = effective_firestore_database_id(
        app_env=settings.APP_ENV,
        configured=getattr(settings, "FIRESTORE_DATABASE_ID", None),
    )
    client = firestore.Client(project=settings.PROJECT_ID, database=database_id)

    doc_ref = client.collection(DASHBOARD_USERS_COLLECTION).document(username)
    doc_ref.set(
        {
            "username": username,
            "clinic_id": args.clinic_id.strip(),
            "password_hash": hash_password(password),
            "active": not args.inactive,
        }
    )
    print(
        f"OK: usuario '{username}' -> clínica '{args.clinic_id.strip()}' "
        f"(base Firestore '{database_id}', activo={not args.inactive})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
