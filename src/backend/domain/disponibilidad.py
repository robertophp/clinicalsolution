from __future__ import annotations

from datetime import datetime, timedelta

from ..services.calendar_service import CalendarServiceError
from . import availability
from .clinics_state import get_clinics_by_id


def _handle_consultar_disponibilidad(clinic_id: str, language: str, args: dict) -> dict:
    """
    Devuelve horas libres reales (Calendar) o teóricas (solo horario) para una fecha.
    Sin clave 'mensaje': Gemini formula la respuesta usando solo horas_disponibles.
    """
    fecha = (args.get("fecha") or "").strip()
    if not fecha:
        if language == "en":
            return {"ok": False, "error": "missing_fecha", "detail": "Parameter fecha (YYYY-MM-DD) is required."}
        return {"ok": False, "error": "missing_fecha", "detail": "Falta el parámetro fecha (YYYY-MM-DD)."}

    try:
        d = datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        if language == "en":
            return {"ok": False, "error": "bad_date", "detail": "fecha must be YYYY-MM-DD."}
        return {"ok": False, "error": "bad_date", "detail": "La fecha debe estar en formato YYYY-MM-DD."}

    today_sv = availability._today_in_el_salvador()
    if d < today_sv:
        if language == "en":
            nota = (
                "That date is already in the past (clinic time: El Salvador, UTC-6). "
                "Offer availability starting from a future day."
            )
        else:
            nota = (
                "Esa fecha ya pasó (hora de la clínica: El Salvador, UTC-6). "
                "Ofrece disponibilidad a partir de un día futuro."
            )
        return {"ok": True, "fecha": fecha, "horas_disponibles": [], "nota": nota}
    if d == today_sv:
        if language == "en":
            nota = (
                "Same-day booking is not available. Appointments require at least one full day's notice in El Salvador time (UTC-6). "
                "The list is empty for today; offer to check times starting tomorrow."
            )
        else:
            nota = (
                "No se ofrecen citas para el mismo día: hace falta al menos un día de anticipación en hora de El Salvador (UTC-6). "
                "horas_disponibles está vacía para hoy; ofrece consultar a partir de mañana."
            )
        return {"ok": True, "fecha": fecha, "horas_disponibles": [], "nota": nota}

    clinic_cfg = get_clinics_by_id().get(clinic_id)
    if clinic_cfg is None:
        return {"ok": False, "error": "unknown_clinic"}

    opening_ranges = availability._opening_ranges_for_day(clinic_cfg, d)
    if not opening_ranges:
        if language == "en":
            nota = "The clinic is closed on that weekday; no appointment start times."
        else:
            nota = "La clínica no atiende ese día de la semana; no hay horas de cita."
        return {"ok": True, "fecha": fecha, "horas_disponibles": [], "nota": nota}

    if clinic_cfg.calendar_sync_enabled and (clinic_cfg.calendar_id or "").strip():
        try:
            horas = availability._available_hourly_slots_for_clinic(clinic_cfg, fecha)
        except CalendarServiceError as exc:
            if language == "en":
                return {
                    "ok": False,
                    "error": "calendar_read_failed",
                    "detail": str(exc),
                }
            return {
                "ok": False,
                "error": "calendar_read_failed",
                "detail": str(exc),
            }
        return {
            "ok": True,
            "fecha": fecha,
            "fuente": "google_calendar",
            "horas_disponibles": horas,
            "nota": (
                "Only offer these exact start times (HH:00). Do not add any other time."
                if language == "en"
                else "Solo ofrece estas horas de inicio exactas (HH:00). No añadas ninguna otra."
            ),
        }

    horas = availability._theoretical_hourly_slots_for_clinic(clinic_cfg, fecha)
    if language == "en":
        adv = (
            "Calendar integration is off for this clinic: times follow opening hours only and may not reflect "
            "real-time bookings."
        )
    else:
        adv = (
            "Esta clínica no tiene sincronización con Google Calendar activa: las horas siguen solo el horario "
            "publicado y pueden no reflejar ocupación real."
        )
    return {
        "ok": True,
        "fecha": fecha,
        "fuente": "solo_horario_sin_calendario",
        "horas_disponibles": horas,
        "advertencia": adv,
        "nota": (
            "Only offer these exact start times unless you warn that the list is not from Calendar."
            if language == "en"
            else "Solo ofrece estas horas exactas y menciona que no provienen del calendario en tiempo real."
        ),
    }


def _handle_consultar_primer_dia_disponible(clinic_id: str, language: str, args: dict) -> dict:
    """
    Primer día (>= mañana en El Salvador) con al menos una hora libre, hasta max_días calendario.
    Devuelve primeras_tres_horas para ofrecer al paciente en flujos de dolor/urgencia.
    """
    raw_max = args.get("max_dias")
    try:
        max_dias = int(raw_max) if raw_max is not None else 14
    except (TypeError, ValueError):
        max_dias = 14
    max_dias = max(1, min(max_dias, 30))

    clinic_cfg = get_clinics_by_id().get(clinic_id)
    if clinic_cfg is None:
        return {"ok": False, "error": "unknown_clinic"}

    today_sv = availability._today_in_el_salvador()
    d = today_sv + timedelta(days=1)

    for _ in range(max_dias):
        fecha = d.isoformat()
        opening_ranges = availability._opening_ranges_for_day(clinic_cfg, d)
        if not opening_ranges:
            d += timedelta(days=1)
            continue

        if clinic_cfg.calendar_sync_enabled and (clinic_cfg.calendar_id or "").strip():
            try:
                horas = availability._available_hourly_slots_for_clinic(clinic_cfg, fecha)
            except CalendarServiceError as exc:
                if language == "en":
                    return {
                        "ok": False,
                        "error": "calendar_read_failed",
                        "detail": str(exc),
                    }
                return {
                    "ok": False,
                    "error": "calendar_read_failed",
                    "detail": str(exc),
                }
        else:
            horas = availability._theoretical_hourly_slots_for_clinic(clinic_cfg, fecha)

        if horas:
            primeras_tres = horas[:3]
            if language == "en":
                nota = (
                    "This is the first day from tomorrow with at least one free slot. "
                    "For pain/urgency flows: reply with brief empathy, reassure them expert care will help, "
                    "then offer ONLY these first three start times (primeras_tres_horas) unless they ask for more. "
                    "Default service suggestion: evaluacion from the catalog; if they want something else, continue the normal booking flow."
                )
            else:
                nota = (
                    "Es el primer día (desde mañana) con al menos un hueco disponible. "
                    "En casos de dolor/urgencia: responde con empatía breve, tranquiliza (está en manos del equipo) "
                    "y ofrece SOLO las tres primeras horas de primeras_tres_horas salvo que pida más detalle. "
                    "Sugiere por defecto el servicio evaluacion del catálogo; si quiere otro servicio, sigue el flujo normal de agendado."
                )
            fuente = "google_calendar" if clinic_cfg.calendar_sync_enabled else "solo_horario_sin_calendario"
            out: dict = {
                "ok": True,
                "fecha": fecha,
                "fuente": fuente,
                "horas_disponibles": horas,
                "primeras_tres_horas": primeras_tres,
                "nota": nota,
            }
            if not clinic_cfg.calendar_sync_enabled and language == "en":
                out["advertencia"] = (
                    "Calendar sync is off: slots follow opening hours only and may not reflect real-time bookings."
                )
            elif not clinic_cfg.calendar_sync_enabled:
                out["advertencia"] = (
                    "Sin sincronización con Google Calendar: las horas siguen el horario publicado y pueden no reflejar ocupación real."
                )
            return out

        d += timedelta(days=1)

    if language == "en":
        msg = (
            f"No free start times were found in the next {max_dias} calendar days starting tomorrow. "
            "Apologize briefly and ask the patient to call the clinic."
        )
    else:
        msg = (
            f"No se encontraron horas libres en los próximos {max_dias} días calendario desde mañana. "
            "Pide disculpas breves y sugiere llamar a la clínica."
        )
    return {"ok": False, "error": "sin_disponibilidad", "mensaje": msg}
