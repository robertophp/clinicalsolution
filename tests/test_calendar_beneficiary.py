"""Calendar event payload con beneficiario."""

from types import SimpleNamespace

from backend.services.calendar_service import CalendarService


def test_calendar_summary_uses_beneficiary_name():
    cita = SimpleNamespace(
        fecha_cita=__import__("datetime").date(2026, 5, 15),
        hora_cita=__import__("datetime").time(10, 0),
        es_para_tercero=True,
        paciente_nombre="Roberto Menjivar",
        nombre_secundario="María López",
        razon_cita="limpieza_dental",
        telefono="+50370000001",
        clinic_id="demo_clinic_1",
    )
    event = CalendarService._build_event_payload(
        cita=cita,
        clinic_name="Clínica demo",
        assistant_name="Bernardo",
        servicio_display="Limpieza dental",
    )
    assert "María López" in event["summary"]
    assert "Roberto Menjivar" not in event["summary"]
    desc = event["description"]
    assert "Agendado para tercero: sí" in desc
    assert "Contacto WhatsApp: Roberto Menjivar" in desc
