"""
Pruebas de _handle_agendar_cita y _handle_cancelar_cita con mocks (sin BigQuery ni Calendar).

Si el flujo falla, pytest.fail muestra la respuesta completa del handler (incluye 'error').
"""

from __future__ import annotations

from datetime import date, time
from unittest.mock import MagicMock, patch

import pytest

from backend.domain.citas_handlers import (
    _handle_agendar_cita,
    _handle_cancelar_cita,
    _handle_listar_mis_citas_proximas,
    set_conversation_memory_for_cita_handlers,
)
from backend.repositories import CITA_STATUS_CANCELADA
from backend.schemas import ClinicConfig


def _fail_if_agendar_not_ok(out: dict, *, context: str = "") -> None:
    if out.get("error"):
        pytest.fail(f"{context} agendar_cita devolvió error={out.get('error')!r} respuesta completa={out!r}")
    if "mensaje" not in out:
        pytest.fail(f"{context} falta 'mensaje' en respuesta: {out!r}")
    msg = (out.get("mensaje") or "").lower()
    if "agendado" not in msg and "scheduled" not in msg:
        pytest.fail(f"{context} mensaje inesperado (esperaba confirmación de cita): {out!r}")


def _fail_if_cancelar_not_ok(out: dict, *, context: str = "") -> None:
    if out.get("error"):
        pytest.fail(f"{context} cancelar_cita devolvió error={out.get('error')!r} respuesta completa={out!r}")
    if "mensaje" not in out:
        pytest.fail(f"{context} falta 'mensaje' en respuesta: {out!r}")
    msg = (out.get("mensaje") or "").lower()
    if "cancelada" not in msg and "cancelled" not in msg:
        pytest.fail(f"{context} mensaje inesperado (esperaba confirmación de cancelación): {out!r}")


@pytest.fixture
def memory_svc():
    m = MagicMock()
    m.get_metadata.return_value = {}
    m.set_patient_name.return_value = None
    m.try_lock_cita_slot.return_value = True
    set_conversation_memory_for_cita_handlers(m)
    return m


@pytest.fixture
def demo_clinic_sin_calendar() -> dict[str, ClinicConfig]:
    """Misma clínica que el catálogo de servicios (demo_clinic_1), sin sync a Calendar."""
    return {
        "demo_clinic_1": ClinicConfig(
            id="demo_clinic_1",
            name="Clínica demo",
            system_prompt="x",
            opening_hours={
                "mon_fri": {"days": ["mon", "tue", "wed", "thu", "fri"], "from": "08:00", "to": "17:00"}
            },
            calendar_sync_enabled=False,
            calendar_id=None,
        ),
    }


def test_agendar_cita_handler_ok_sin_bigquery_ni_calendar(memory_svc, demo_clinic_sin_calendar):
    """Reserva: BigQuery simulado con create_cita mockeado; sin evento en Google Calendar."""
    fake_cita = MagicMock()
    fake_cita.paciente_nombre = "Ana Prueba"
    fake_cita.calendar_event_id = None
    fake_cita.calendar_id = None

    db_mock = MagicMock()

    with (
        patch("backend.domain.availability._today_in_el_salvador", return_value=date(2026, 5, 13)),
        patch("backend.domain.citas_handlers.get_clinics_by_id", return_value=demo_clinic_sin_calendar),
        patch("backend.domain.citas_handlers.get_latest_self_cita_for_phone", return_value=None),
        patch("backend.domain.citas_handlers.SessionLocal", return_value=db_mock),
        patch("backend.domain.citas_handlers.count_activa_citas_at_slot", return_value=0),
        patch("backend.domain.citas_handlers.create_cita", return_value=fake_cita) as mock_create,
    ):
        out = _handle_agendar_cita(
            from_number="whatsapp:+50370000001",
            clinic_id="demo_clinic_1",
            language="es",
            assistant_name="Asistente Test",
            args={
                "nombre": "Ana",
                "fecha": "2026-05-15",
                "hora": "13:00",
                "servicio": "limpieza_dental",
            },
        )

    _fail_if_agendar_not_ok(out, context="[agendar ok]")
    mock_create.assert_called_once()
    assert db_mock.close.call_count == 2
    memory_svc.try_lock_cita_slot.assert_called_once_with("demo_clinic_1", "2026-05-15", "13:00")
    memory_svc.release_cita_slot.assert_called_once_with("demo_clinic_1", "2026-05-15", "13:00")


def test_agendar_cita_para_tercero_no_set_patient_name(memory_svc, demo_clinic_sin_calendar):
    fake_cita = MagicMock()
    fake_cita.paciente_nombre = None
    fake_cita.nombre_secundario = "María López"
    fake_cita.es_para_tercero = True

    db_mock = MagicMock()

    with (
        patch("backend.domain.availability._today_in_el_salvador", return_value=date(2026, 5, 13)),
        patch("backend.domain.citas_handlers.get_clinics_by_id", return_value=demo_clinic_sin_calendar),
        patch("backend.domain.citas_handlers.SessionLocal", return_value=db_mock),
        patch("backend.domain.citas_handlers.count_activa_citas_at_slot", return_value=0),
        patch("backend.domain.citas_handlers.create_cita", return_value=fake_cita) as mock_create,
    ):
        out = _handle_agendar_cita(
            from_number="whatsapp:+50370000010",
            clinic_id="demo_clinic_1",
            language="es",
            assistant_name="Asistente Test",
            args={
                "nombre": "María López",
                "fecha": "2026-05-15",
                "hora": "13:00",
                "servicio": "limpieza_dental",
                "es_para_tercero": True,
                "nombre_titular": "Roberto Menjivar",
            },
        )

    _fail_if_agendar_not_ok(out, context="[tercero]")
    assert "María López" in out["mensaje"]
    memory_svc.set_patient_name.assert_not_called()
    mock_create.assert_called_once()
    _, kwargs = mock_create.call_args
    assert kwargs["es_para_tercero"] is True
    assert kwargs["nombre_secundario"] == "María López"
    assert kwargs["paciente_nombre"] == "Roberto Menjivar"


def test_cancelar_cita_handler_ok_sin_calendar(memory_svc, demo_clinic_sin_calendar):
    """Cancelación con cita activa mockeada; no llama a Calendar si no hay event_id."""
    cita_activa = MagicMock()
    cita_activa.calendar_id = None
    cita_activa.calendar_event_id = None

    db_mock = MagicMock()

    with (
        patch("backend.domain.citas_handlers.get_clinics_by_id", return_value=demo_clinic_sin_calendar),
        patch("backend.domain.citas_handlers.SessionLocal", return_value=db_mock),
        patch("backend.domain.citas_handlers.get_latest_activa_cita_for_phone", return_value=cita_activa),
        patch("backend.domain.citas_handlers.update_cita_status") as mock_update,
        patch("backend.domain.citas_handlers.calendar_service.delete_event") as mock_del,
    ):
        out = _handle_cancelar_cita(
            from_number="whatsapp:+50370000001",
            clinic_id="demo_clinic_1",
            language="es",
        )

    _fail_if_cancelar_not_ok(out, context="[cancelar ok]")
    mock_update.assert_called_once_with(db_mock, cita_activa, CITA_STATUS_CANCELADA)
    mock_del.assert_not_called()
    db_mock.close.assert_called_once()


def test_flujo_agendar_y_cancelar_en_secuencia(memory_svc, demo_clinic_sin_calendar):
    """Una reserva simulada y luego cancelación; si algo falla se muestra el dict completo."""
    fake_cita = MagicMock()
    fake_cita.paciente_nombre = "Luis Prueba"
    fake_cita.calendar_id = None
    fake_cita.calendar_event_id = None

    db_mock = MagicMock()

    with (
        patch("backend.domain.availability._today_in_el_salvador", return_value=date(2026, 5, 13)),
        patch("backend.domain.citas_handlers.get_clinics_by_id", return_value=demo_clinic_sin_calendar),
        patch("backend.domain.citas_handlers.get_latest_self_cita_for_phone", return_value=None),
        patch("backend.domain.citas_handlers.SessionLocal", return_value=db_mock),
        patch("backend.domain.citas_handlers.count_activa_citas_at_slot", return_value=0),
        patch("backend.domain.citas_handlers.create_cita", return_value=fake_cita),
    ):
        out_agendar = _handle_agendar_cita(
            from_number="whatsapp:+50370000002",
            clinic_id="demo_clinic_1",
            language="es",
            assistant_name="Asistente Test",
            args={
                "nombre": "Luis",
                "fecha": "2026-05-15",
                "hora": "10:00",
                "servicio": "limpieza_dental",
            },
        )

    _fail_if_agendar_not_ok(out_agendar, context="[secuencia agendar]")

    with (
        patch("backend.domain.citas_handlers.get_clinics_by_id", return_value=demo_clinic_sin_calendar),
        patch("backend.domain.citas_handlers.SessionLocal", return_value=db_mock),
        patch("backend.domain.citas_handlers.get_latest_activa_cita_for_phone", return_value=fake_cita),
        patch("backend.domain.citas_handlers.update_cita_status"),
    ):
        out_cancel = _handle_cancelar_cita(
            from_number="whatsapp:+50370000002",
            clinic_id="demo_clinic_1",
            language="es",
        )

    _fail_if_cancelar_not_ok(out_cancel, context="[secuencia cancelar]")


def test_agendar_cita_muestra_error_cuando_create_cita_falla(memory_svc, demo_clinic_sin_calendar):
    """Si BigQuery (create_cita) falla, la respuesta incluye 'error' con el mensaje de excepción."""
    db_mock = MagicMock()
    boom = RuntimeError("simulated BigQuery insert failure")

    with (
        patch("backend.domain.availability._today_in_el_salvador", return_value=date(2026, 5, 13)),
        patch("backend.domain.citas_handlers.get_clinics_by_id", return_value=demo_clinic_sin_calendar),
        patch("backend.domain.citas_handlers.get_latest_self_cita_for_phone", return_value=None),
        patch("backend.domain.citas_handlers.SessionLocal", return_value=db_mock),
        patch("backend.domain.citas_handlers.count_activa_citas_at_slot", return_value=0),
        patch("backend.domain.citas_handlers.create_cita", side_effect=boom),
    ):
        out = _handle_agendar_cita(
            from_number="whatsapp:+50370000003",
            clinic_id="demo_clinic_1",
            language="es",
            assistant_name="Asistente Test",
            args={
                "nombre": "Pedro",
                "fecha": "2026-05-15",
                "hora": "11:00",
                "servicio": "limpieza_dental",
            },
        )

    assert "error" in out, f"Se esperaba clave 'error' en la respuesta: {out!r}"
    assert boom.args[0] in str(out["error"]), f"El error debería propagarse en 'error': {out!r}"
    assert "mensaje" in out, f"Falta mensaje para el usuario: {out!r}"
    memory_svc.release_cita_slot.assert_called_once_with("demo_clinic_1", "2026-05-15", "11:00")


def test_agendar_cita_no_lock_devuelve_error_sin_insertar(memory_svc, demo_clinic_sin_calendar):
    """Si otro request tiene el lock del slot, no debe llamar a create_cita ni tocar BigQuery."""
    memory_svc.try_lock_cita_slot.return_value = False
    db_mock = MagicMock()

    with (
        patch("backend.domain.availability._today_in_el_salvador", return_value=date(2026, 5, 13)),
        patch("backend.domain.citas_handlers.get_clinics_by_id", return_value=demo_clinic_sin_calendar),
        patch("backend.domain.citas_handlers.get_latest_self_cita_for_phone", return_value=None),
        patch("backend.domain.citas_handlers.SessionLocal", return_value=db_mock),
        patch("backend.domain.citas_handlers.create_cita") as mock_create,
    ):
        out = _handle_agendar_cita(
            from_number="whatsapp:+50370000004",
            clinic_id="demo_clinic_1",
            language="es",
            assistant_name="Asistente Test",
            args={
                "nombre": "Carla",
                "fecha": "2026-05-15",
                "hora": "14:00",
                "servicio": "limpieza_dental",
            },
        )

    assert out.get("error") == "Slot en proceso de reserva", f"Respuesta inesperada: {out!r}"
    mock_create.assert_not_called()
    memory_svc.release_cita_slot.assert_not_called()


def test_agendar_cita_cupo_lleno_en_recheck_devuelve_error_sin_insertar(memory_svc, demo_clinic_sin_calendar):
    """Si el re-chequeo de cupo (dentro del lock) ya está lleno, no debe insertar la cita."""
    db_mock = MagicMock()

    with (
        patch("backend.domain.availability._today_in_el_salvador", return_value=date(2026, 5, 13)),
        patch("backend.domain.citas_handlers.get_clinics_by_id", return_value=demo_clinic_sin_calendar),
        patch("backend.domain.citas_handlers.get_latest_self_cita_for_phone", return_value=None),
        patch("backend.domain.citas_handlers.SessionLocal", return_value=db_mock),
        patch("backend.domain.citas_handlers.count_activa_citas_at_slot", return_value=1),
        patch("backend.domain.citas_handlers.create_cita") as mock_create,
    ):
        out = _handle_agendar_cita(
            from_number="whatsapp:+50370000005",
            clinic_id="demo_clinic_1",
            language="es",
            assistant_name="Asistente Test",
            args={
                "nombre": "Diego",
                "fecha": "2026-05-15",
                "hora": "15:00",
                "servicio": "limpieza_dental",
            },
        )

    assert out.get("error") == "Cupo lleno", f"Respuesta inesperada: {out!r}"
    mock_create.assert_not_called()
    memory_svc.release_cita_slot.assert_called_once_with("demo_clinic_1", "2026-05-15", "15:00")


def test_cancelar_sin_cita_activa_devuelve_error_claro(memory_svc, demo_clinic_sin_calendar):
    db_mock = MagicMock()
    with (
        patch("backend.domain.citas_handlers.get_clinics_by_id", return_value=demo_clinic_sin_calendar),
        patch("backend.domain.citas_handlers.SessionLocal", return_value=db_mock),
        patch("backend.domain.citas_handlers.get_latest_activa_cita_for_phone", return_value=None),
    ):
        out = _handle_cancelar_cita(
            from_number="whatsapp:+50379999999",
            clinic_id="demo_clinic_1",
            language="es",
        )
    assert out.get("error") == "Sin cita activa", f"Respuesta inesperada: {out!r}"
    assert "no tienes" in (out.get("mensaje") or "").lower(), f"Mensaje inesperado: {out!r}"


def test_listar_mis_citas_proximas_devuelve_filas(memory_svc, demo_clinic_sin_calendar):
    c1 = MagicMock()
    c1.fecha_cita = date(2026, 5, 20)
    c1.hora_cita = time(10, 0)
    c1.razon_cita = "limpieza_dental"
    db_mock = MagicMock()
    with (
        patch("backend.domain.citas_handlers.get_clinics_by_id", return_value=demo_clinic_sin_calendar),
        patch("backend.domain.citas_handlers.SessionLocal", return_value=db_mock),
        patch(
            "backend.domain.citas_handlers.list_upcoming_activa_citas_for_phone",
            return_value=[c1],
        ),
    ):
        out = _handle_listar_mis_citas_proximas(
            from_number="whatsapp:+50370000001",
            clinic_id="demo_clinic_1",
            language="es",
        )
    assert out.get("ok") is True, out
    assert len(out.get("citas") or []) == 1
    row = out["citas"][0]
    assert row["fecha"] == "2026-05-20"
    assert row["hora"] == "10:00"
    assert row.get("servicio_id") == "limpieza_dental"
    assert "nota" in out
    db_mock.close.assert_called_once()


def test_listar_mis_citas_proximas_vacio(memory_svc, demo_clinic_sin_calendar):
    db_mock = MagicMock()
    with (
        patch("backend.domain.citas_handlers.get_clinics_by_id", return_value=demo_clinic_sin_calendar),
        patch("backend.domain.citas_handlers.SessionLocal", return_value=db_mock),
        patch("backend.domain.citas_handlers.list_upcoming_activa_citas_for_phone", return_value=[]),
    ):
        out = _handle_listar_mis_citas_proximas(
            from_number="whatsapp:+50370000001",
            clinic_id="demo_clinic_1",
            language="en",
        )
    assert out.get("ok") is True
    assert out.get("citas") == []
    assert "empty" in (out.get("nota") or "").lower() or "patient" in (out.get("nota") or "").lower()
