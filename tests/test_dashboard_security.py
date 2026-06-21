"""Hashing de contraseñas y tokens de sesión firmados del dashboard."""

from backend.services.dashboard_security import (
    hash_password,
    sign_session,
    verify_password,
    verify_session,
)


def test_hash_and_verify_password_roundtrip():
    h = hash_password("ClaveFuerte#123")
    assert h.startswith("pbkdf2_sha256$")
    assert verify_password("ClaveFuerte#123", h)
    assert not verify_password("otra", h)


def test_verify_password_rejects_malformed_hash():
    assert not verify_password("x", "no-es-un-hash")
    assert not verify_password("x", "")
    assert not verify_password("", "pbkdf2_sha256$1$a$b")


def test_hash_is_salted_unique():
    assert hash_password("misma") != hash_password("misma")


def test_session_sign_and_verify_roundtrip():
    token = sign_session(
        clinic_id="demo_clinic_1", username="doctora", secret="s3cr3t", ttl_minutes=60, now=1000
    )
    payload = verify_session(token, secret="s3cr3t", now=1000)
    assert payload is not None
    assert payload["clinic_id"] == "demo_clinic_1"
    assert payload["username"] == "doctora"


def test_session_expires():
    token = sign_session(
        clinic_id="c1", username="u", secret="s3cr3t", ttl_minutes=1, now=1000
    )
    # 61 segundos después => expirada
    assert verify_session(token, secret="s3cr3t", now=1061) is None
    # justo antes => válida
    assert verify_session(token, secret="s3cr3t", now=1059) is not None


def test_session_rejects_wrong_secret_and_tampering():
    token = sign_session(clinic_id="c1", username="u", secret="right", ttl_minutes=60, now=1000)
    assert verify_session(token, secret="wrong", now=1000) is None

    payload_b64, sig = token.split(".")
    tampered = payload_b64 + "x." + sig
    assert verify_session(tampered, secret="right", now=1000) is None
    assert verify_session("", secret="right", now=1000) is None
    assert verify_session("nodot", secret="right", now=1000) is None
