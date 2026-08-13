"""
Seguridad del dashboard: hashing de contraseñas y tokens de sesión firmados.

Sin dependencias externas (solo stdlib):
- Contraseñas: PBKDF2-HMAC-SHA256 con salt aleatorio (formato ``pbkdf2_sha256$iter$salt$hash``).
- Sesión: token firmado con HMAC-SHA256 que transporta ``clinic_id``, ``username`` y ``exp``.

El ``clinic_id`` viaja DENTRO del token firmado: el servidor nunca confía en un
``clinic_id`` enviado por el cliente, garantizando el aislamiento por clínica.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

PBKDF2_ITERATIONS = 200_000
_ALGO = "pbkdf2_sha256"


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Devuelve un hash con salt para almacenar (formato ``pbkdf2_sha256$iter$salt$hash``)."""
    if not password:
        raise ValueError("password vacío")
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${_b64e(salt)}${_b64e(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """Verifica una contraseña contra el hash almacenado (comparación en tiempo constante)."""
    if not password or not stored:
        return False
    try:
        algo, iter_s, salt_b64, hash_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        iterations = int(iter_s)
        salt = _b64d(salt_b64)
        expected = _b64d(hash_b64)
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


def sign_session(
    *,
    clinic_id: str,
    username: str,
    secret: str,
    ttl_minutes: int,
    now: int | None = None,
) -> str:
    """Crea un token de sesión firmado (``payload.signature``) con expiración."""
    if not secret:
        raise ValueError("secret de sesión no configurado")
    issued = int(now if now is not None else time.time())
    payload = {
        "clinic_id": clinic_id,
        "username": username,
        "exp": issued + ttl_minutes * 60,
    }
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64e(sig)}"


def verify_session(token: str, *, secret: str, now: int | None = None) -> dict | None:
    """
    Valida firma y expiración. Devuelve el payload (dict) si es válido, ``None`` si no.
    """
    if not token or not secret:
        return None
    try:
        payload_b64, sig_b64 = token.split(".")
    except ValueError:
        return None
    expected_sig = hmac.new(
        secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        given_sig = _b64d(sig_b64)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(expected_sig, given_sig):
        return None
    try:
        payload = json.loads(_b64d(payload_b64))
    except (ValueError, TypeError):
        return None
    exp = payload.get("exp")
    current = int(now if now is not None else time.time())
    if not isinstance(exp, int) or current >= exp:
        return None
    if not payload.get("clinic_id"):
        return None
    return payload


__all__ = [
    "hash_password",
    "verify_password",
    "sign_session",
    "verify_session",
    "PBKDF2_ITERATIONS",
]
