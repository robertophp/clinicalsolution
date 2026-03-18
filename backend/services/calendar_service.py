from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ..database import Cita


CALENDAR_TIMEZONE = "America/El_Salvador"


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
    def _build_event_payload(
        *,
        cita: Cita,
        clinic_name: str,
        assistant_name: str,
    ) -> dict:
        """
        Construye el dict de evento de Calendar a partir de una cita.

        - Usa hora local de El Salvador.
        - Duración fija de 60 minutos (puedes ajustar si lo necesitas).
        """
        if not cita.fecha_cita or not cita.hora_cita:
            raise CalendarServiceError("La cita no tiene fecha u hora para Calendar.")

        start_dt = datetime.combine(cita.fecha_cita, cita.hora_cita)
        end_dt = start_dt + timedelta(minutes=60)

        paciente = (cita.paciente_nombre or "Paciente sin nombre").strip()
        servicio = (cita.razon_cita or "Servicio sin especificar").strip()
        telefono = (cita.telefono or "Sin teléfono").strip()
        clinic_id = (cita.clinic_id or "sin_clinica").strip()

        summary = f"Cita: {paciente} – {servicio}"

        description_lines = [
            f"Clínica: {clinic_name} (ID: {clinic_id})",
            f"Paciente: {paciente}",
            f"Teléfono: {telefono}",
            f"Servicio: {servicio}",
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


calendar_service = CalendarService()

