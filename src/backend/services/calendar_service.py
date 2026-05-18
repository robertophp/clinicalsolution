from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta, time as time_type, timezone
from typing import Any, Optional

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ..database import Cita


CALENDAR_TIMEZONE = "America/El_Salvador"
# Google Calendar permite títulos largos; acortamos para legibilidad en móvil.
CALENDAR_SUMMARY_MAX_LEN = 200


class CalendarServiceError(Exception):
    """Errores de integración con Google Calendar."""


class CalendarService:
    """
    Servicio simple para sincronizar citas con Google Calendar.

    - Usa las credenciales configuradas en el entorno (GOOGLE_APPLICATION_CREDENTIALS o ADC).
    - Trabaja sobre un calendario por clínica (calendar_id).
    """

    def __init__(self) -> None:
        self._service = None

    def _get_client(self):
        if self._service is not None:
            return self._service

        # Usa credenciales por defecto de Google (GOOGLE_APPLICATION_CREDENTIALS o ADC).
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/calendar"]
        )
        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    @staticmethod
    def _truncate_calendar_summary(text: str, max_len: int = CALENDAR_SUMMARY_MAX_LEN) -> str:
        t = (text or "").strip()
        if len(t) <= max_len:
            return t
        if max_len <= 1:
            return "…"
        return t[: max_len - 1] + "…"

    @staticmethod
    def _build_event_payload(
        *,
        cita: Cita,
        clinic_name: str,
        assistant_name: str,
        servicio_display: str | None = None,
        calendar_suffix: str | None = None,
    ) -> dict:
        """
        Construye el dict de evento de Calendar a partir de una cita.

        - Usa hora local de El Salvador.
        - Duración fija de 60 minutos (puedes ajustar si lo necesitas).
        - servicio_display: etiqueta legible del servicio (si no, se usa razon_cita).
        - calendar_suffix: texto corto para urgencias (ej. dolor post cita).
        """
        if not cita.fecha_cita or not cita.hora_cita:
            raise CalendarServiceError("La cita no tiene fecha u hora para Calendar.")

        start_dt = datetime.combine(cita.fecha_cita, cita.hora_cita)
        end_dt = start_dt + timedelta(minutes=60)

        paciente = (cita.paciente_nombre or "Paciente sin nombre").strip()
        razon = (cita.razon_cita or "Servicio sin especificar").strip()
        servicio_label = (servicio_display or "").strip() or razon
        telefono = (cita.telefono or "Sin teléfono").strip()
        clinic_id = (cita.clinic_id or "sin_clinica").strip()

        suf = (calendar_suffix or "").strip()
        if suf:
            summary_raw = f"Cita: {paciente} – {servicio_label} – {suf}"
        else:
            summary_raw = f"Cita: {paciente} – {servicio_label}"
        summary = CalendarService._truncate_calendar_summary(summary_raw)

        description_lines = [
            f"Clínica: {clinic_name} (ID: {clinic_id})",
            f"Paciente: {paciente}",
            f"Teléfono: {telefono}",
            f"Servicio (catálogo): {razon}",
            f"Etiqueta en agenda: {servicio_label}",
            "",
            f"Agendado por: Asistente WhatsApp {assistant_name}",
            "Doctor asignado: (pendiente)",
            "",
            "IMPORTANTE: Esta cita fue agendada por el asistente de WhatsApp.",
            "No modificarla directamente desde el calendario.",
            "Cualquier cambio (reagendar o cancelar) debe hacerse por WhatsApp.",
        ]

        event = {
            "summary": summary,
            "description": "\n".join(description_lines),
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": CALENDAR_TIMEZONE,
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": CALENDAR_TIMEZONE,
            },
        }
        return event

    def create_event_for_cita(
        self,
        *,
        calendar_id: str,
        cita: Cita,
        clinic_name: str,
        assistant_name: str,
        servicio_display: str | None = None,
        calendar_suffix: str | None = None,
    ) -> str:
        """
        Crea un evento en Google Calendar para la cita y devuelve el event_id.
        """
        try:
            client = self._get_client()
            body = self._build_event_payload(
                cita=cita,
                clinic_name=clinic_name,
                assistant_name=assistant_name,
                servicio_display=servicio_display,
                calendar_suffix=calendar_suffix,
            )
            created = (
                client.events()
                .insert(calendarId=calendar_id, body=body)
                .execute()
            )
            event_id: Optional[str] = created.get("id")
            if not event_id:
                raise CalendarServiceError("Google Calendar no devolvió un event_id.")
            return event_id
        except HttpError as exc:  # type: ignore[import-untyped]
            raise CalendarServiceError(f"Error de Google Calendar al crear evento: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise CalendarServiceError(f"Error inesperado al crear evento en Calendar: {exc}") from exc

    def delete_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
    ) -> None:
        """
        Elimina (o marca como cancelado) un evento de Calendar.
        """
        if not calendar_id or not event_id:
            return

        try:
            client = self._get_client()
            client.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        except HttpError as exc:  # type: ignore[import-untyped]
            # Si el evento ya no existe, no consideramos que sea un error crítico.
            if exc.resp is not None and getattr(exc.resp, "status", None) == 404:
                return
            raise CalendarServiceError(f"Error de Google Calendar al borrar evento: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise CalendarServiceError(f"Error inesperado al borrar evento en Calendar: {exc}") from exc

    def get_event(self, *, calendar_id: str, event_id: str) -> dict[str, Any] | None:
        """
        Obtiene un evento por ID. Devuelve None si no existe (404).
        """
        if not calendar_id or not event_id:
            return None
        try:
            client = self._get_client()
            return (
                client.events()
                .get(calendarId=calendar_id, eventId=event_id)
                .execute()
            )
        except HttpError as exc:  # type: ignore[import-untyped]
            if exc.resp is not None and getattr(exc.resp, "status", None) == 404:
                return None
            raise CalendarServiceError(f"Error de Google Calendar al leer evento: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise CalendarServiceError(f"Error inesperado al leer evento en Calendar: {exc}") from exc

    @staticmethod
    def _parse_event_datetime(value: str) -> datetime | None:
        s = (value or "").strip().replace("Z", "+00:00")
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def list_busy_intervals(
        self,
        *,
        calendar_id: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> list[tuple[datetime, datetime]]:
        """
        Lista intervalos ocupados de eventos en [start_dt, end_dt).
        """
        if not calendar_id:
            return []
        try:
            client = self._get_client()
            resp = (
                client.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=start_dt.astimezone(timezone.utc).isoformat(),
                    timeMax=end_dt.astimezone(timezone.utc).isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except HttpError as exc:  # type: ignore[import-untyped]
            raise CalendarServiceError(f"Error de Google Calendar al listar eventos: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise CalendarServiceError(f"Error inesperado al listar eventos de Calendar: {exc}") from exc

        out: list[tuple[datetime, datetime]] = []
        for item in (resp.get("items") or []):
            if (item.get("status") or "").lower() == "cancelled":
                continue
            start_raw = (item.get("start") or {}).get("dateTime")
            end_raw = (item.get("end") or {}).get("dateTime")
            if not start_raw or not end_raw:
                continue
            ev_start = self._parse_event_datetime(start_raw)
            ev_end = self._parse_event_datetime(end_raw)
            if not ev_start or not ev_end or ev_end <= ev_start:
                continue
            out.append((ev_start, ev_end))
        return out

    @staticmethod
    def _ceil_to_next_hour(dt: datetime) -> datetime:
        if dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
            return dt
        return (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))

    def get_available_hourly_slots(
        self,
        *,
        calendar_id: str,
        day: date_type,
        opening_ranges: list[tuple[time_type, time_type]],
        slot_minutes: int = 60,
        use_calendar_busy: bool = True,
    ) -> list[time_type]:
        """
        Devuelve horas de inicio disponibles en punto para slots de 60 minutos.
        Regla: el slot debe caber completo dentro del bloque (start+slot <= cierre).

        Si ``use_calendar_busy`` es True y hay ``calendar_id``, descarta traslapes con
        eventos reales de Google Calendar. Si es False, solo aplica el horario de la
        clínica (útil cuando aún no hay integración con Calendar).
        """
        if not opening_ranges:
            return []
        tz_sv = timezone(timedelta(hours=-6))
        day_windows: list[tuple[datetime, datetime]] = []
        for start_t, end_t in opening_ranges:
            win_start = datetime.combine(day, start_t, tzinfo=tz_sv)
            win_end = datetime.combine(day, end_t, tzinfo=tz_sv)
            if win_end <= win_start:
                continue
            day_windows.append((win_start, win_end))
        if not day_windows:
            return []

        query_start = min(w[0] for w in day_windows)
        query_end = max(w[1] for w in day_windows)
        if use_calendar_busy and calendar_id:
            busy = self.list_busy_intervals(calendar_id=calendar_id, start_dt=query_start, end_dt=query_end)
        else:
            busy = []

        available: list[time_type] = []
        slot_delta = timedelta(minutes=slot_minutes)
        for win_start, win_end in day_windows:
            cursor = self._ceil_to_next_hour(win_start)
            while cursor + slot_delta <= win_end:
                slot_end = cursor + slot_delta
                overlaps = any((cursor < b_end and slot_end > b_start) for b_start, b_end in busy)
                if not overlaps:
                    available.append(cursor.timetz().replace(tzinfo=None))
                cursor += timedelta(hours=1)
        return available

    @staticmethod
    def event_start_to_sv_date_time(event: dict[str, Any]) -> tuple[date_type, time_type] | None:
        """
        Interpreta start del evento API v3 y devuelve (date, time) en hora local El Salvador (UTC-6),
        alineado con el resto de la app (sin depender de tzdata/zoneinfo).
        """
        start = event.get("start") or {}
        dt_str = start.get("dateTime")
        if dt_str:
            s = (dt_str or "").replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(s)
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            tz_sv = timezone(timedelta(hours=-6))
            local = dt.astimezone(tz_sv)
            t = local.time().replace(microsecond=0)
            return local.date(), t
        day_only = (start.get("date") or "").strip()
        if day_only:
            try:
                d = datetime.strptime(day_only, "%Y-%m-%d").date()
            except ValueError:
                return None
            return d, time_type(0, 0)
        return None


calendar_service = CalendarService()

