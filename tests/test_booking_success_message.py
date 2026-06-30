"""Mensaje de confirmación de cita con dirección."""

from backend.domain.citas_handlers import _format_booking_success_message


def test_booking_success_message_includes_full_address_and_line_break_es() -> None:
    address = (
        "Colonia Médica, Av Max Bloch #171\n"
        "Frente a la iglesia Arzobispado 📍San Salvador, El Salvador"
    )
    msg = _format_booking_success_message(
        language="es",
        fecha="2026-06-13",
        hora="08:00",
        servicio_label="Evaluación",
        clinic_address=address,
    )
    assert "¡Listo! He agendado tu cita para el 2026-06-13 a las 08:00" in msg
    assert "(servicio: Evaluación)." in msg
    assert "\n\nNo olvides que estamos ubicados en" in msg
    assert "📍San Salvador, El Salvador" in msg
    assert msg.endswith("😉")
    assert "https://" not in msg


def test_booking_success_message_without_address_es() -> None:
    msg = _format_booking_success_message(
        language="es",
        fecha="2026-06-13",
        hora="08:00",
        servicio_label="Evaluación",
        clinic_address=None,
    )
    assert msg == "¡Listo! He agendado tu cita para el 2026-06-13 a las 08:00 (servicio: Evaluación)."
    assert "No olvides" not in msg


def test_booking_success_message_en_with_address() -> None:
    msg = _format_booking_success_message(
        language="en",
        fecha="2026-06-13",
        hora="08:00",
        servicio_label="Cleaning",
        clinic_address="123 Main St\nCity 📍",
    )
    assert "Done! I've scheduled your appointment" in msg
    assert "\n\nDon't forget we're located at" in msg
    assert "City 📍" in msg
