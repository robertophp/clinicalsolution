from __future__ import annotations

import threading
import time

from ..services.calendar_service import calendar_service, CalendarServiceError


def _retry_delete_event_async(
    calendar_id: str,
    event_id: str,
    retries: int = 2,
    delay_seconds: float = 5.0,
) -> None:
    """
    Reintenta de forma asíncrona y limitada borrar un evento de Calendar cuando
    la llamada inicial falló por un error de red/transitorio.

    - No bloquea la respuesta al paciente.
    - No corre de forma periódica: solo se dispara cuando hay un error.
    """

    if not calendar_id or not event_id:
        return

    def _worker() -> None:
        for _ in range(max(retries, 0)):
            try:
                calendar_service.delete_event(calendar_id=calendar_id, event_id=event_id)
                break
            except CalendarServiceError:
                time.sleep(max(delay_seconds, 0.1))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
