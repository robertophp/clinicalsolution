"""
Conversation memory using Firestore.
Stores messages per clinic_id + from_number, with TTL and max history limits.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

from google.cloud import firestore
from google.cloud.exceptions import Conflict

from ..config import settings
from ..domain.runtime_env import effective_firestore_database_id

logger = logging.getLogger(__name__)

# Collection name in Firestore
COLLECTION_NAME = "agentmemory"
# Dedup de entregas repetidas del webhook Meta (mismo wamid); TTL opcional en consola GCP.
META_WEBHOOK_WAMID_DEDUP_COLLECTION = "meta_webhook_wamid_dedup"


def _doc_id(clinic_id: str, from_number: str) -> str:
    """Build a safe Firestore document ID from clinic_id and from_number."""
    digits = re.sub(r"\D", "", from_number) or "unknown"
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", clinic_id)
    return f"{safe_id}_{digits}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(t: Any) -> datetime:
    """Convert Firestore timestamp or string to datetime (UTC)."""
    if t is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if hasattr(t, "timestamp"):
        dt = t
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


class ConversationMemoryService:
    """Persists and retrieves conversation history per user/clinic using Firestore."""

    def __init__(self, project_id: str | None = None) -> None:
        self._project_id = project_id or settings.PROJECT_ID
        self._client: firestore.Client | None = None

    @property
    def _db(self) -> firestore.Client:
        if self._client is None:
            database_id = effective_firestore_database_id(
                app_env=settings.APP_ENV,
                configured=getattr(settings, "FIRESTORE_DATABASE_ID", None),
            )
            self._client = firestore.Client(project=self._project_id, database=database_id)
        return self._client

    def get_recent_messages(
        self,
        clinic_id: str,
        from_number: str,
        *,
        limit: int | None = None,
        ttl_minutes: int | None = None,
    ) -> list[dict[str, str]]:
        """
        Returns the last `limit` messages for this user/clinic that are within
        `ttl_minutes` of now (inactivity window). Chronological order (oldest first).
        """
        limit = limit if limit is not None else settings.CONVERSATION_MAX_HISTORY
        ttl_minutes = ttl_minutes if ttl_minutes is not None else settings.CONVERSATION_TTL_MINUTES
        cutoff = _now_utc() - timedelta(minutes=ttl_minutes)

        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        doc = doc_ref.get()
        if not doc or not doc.exists:
            return []

        data = doc.to_dict() or {}
        raw_messages: list[dict[str, Any]] = data.get("messages") or []

        recent = [m for m in raw_messages if _parse_timestamp(m.get("timestamp")) >= cutoff]
        recent = recent[-limit:] if len(recent) > limit else recent
        return [
            {"role": m.get("role", "user"), "content": (m.get("content") or "").strip()}
            for m in recent
            if (m.get("content") or "").strip()
        ]

    def get_metadata(
        self,
        clinic_id: str,
        from_number: str,
    ) -> dict[str, Any]:
        """Return lightweight metadata for this conversation (e.g. language, patient name, updated_at)."""
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        doc = doc_ref.get()
        if not doc or not doc.exists:
            return {}

        data = doc.to_dict() or {}
        metadata: dict[str, Any] = {}
        if "conversation_language" in data:
            metadata["conversation_language"] = data.get("conversation_language")
        if "patient_name" in data:
            metadata["patient_name"] = data.get("patient_name")
        if "patient_first_name" in data:
            metadata["patient_first_name"] = data.get("patient_first_name")
        if "updated_at" in data:
            metadata["updated_at"] = _parse_timestamp(data.get("updated_at"))
        if "human_transfer_phase" in data:
            metadata["human_transfer_phase"] = data.get("human_transfer_phase")
        if "human_transfer_summary" in data:
            metadata["human_transfer_summary"] = data.get("human_transfer_summary")
        if "human_transfer_categories" in data:
            metadata["human_transfer_categories"] = data.get("human_transfer_categories")
        if "booking_phase" in data:
            metadata["booking_phase"] = data.get("booking_phase")
        if "booking_pending" in data:
            metadata["booking_pending"] = data.get("booking_pending")
        if "name_collection_phase" in data:
            metadata["name_collection_phase"] = data.get("name_collection_phase")
        if "last_discussed_service_id" in data:
            metadata["last_discussed_service_id"] = data.get("last_discussed_service_id")
        if "last_discussed_service_at" in data:
            metadata["last_discussed_service_at"] = _parse_timestamp(data.get("last_discussed_service_at"))
        if "cordales_xray_phase" in data:
            metadata["cordales_xray_phase"] = data.get("cordales_xray_phase")
        if "beneficiario_edad" in data:
            metadata["beneficiario_edad"] = data.get("beneficiario_edad")
        if "maxillofacial_transfer_phase" in data:
            metadata["maxillofacial_transfer_phase"] = data.get("maxillofacial_transfer_phase")
        if "emergency_phase" in data:
            metadata["emergency_phase"] = data.get("emergency_phase")
        if "same_day_phase" in data:
            metadata["same_day_phase"] = data.get("same_day_phase")
        if "confusion_count" in data:
            metadata["confusion_count"] = data.get("confusion_count")
        if "confusion_phase" in data:
            metadata["confusion_phase"] = data.get("confusion_phase")
        if "confusion_context" in data:
            metadata["confusion_context"] = data.get("confusion_context")
        if "confusion_menu_retries" in data:
            metadata["confusion_menu_retries"] = data.get("confusion_menu_retries")
        if "confusion_offered_hours" in data:
            metadata["confusion_offered_hours"] = data.get("confusion_offered_hours")
        return metadata

    def add_message(
        self,
        clinic_id: str,
        from_number: str,
        role: str,
        content: str,
    ) -> None:
        """
        Appends a message and trims the stored list to CONVERSATION_MAX_STORED.
        """
        if not content.strip():
            return

        doc_id = _doc_id(clinic_id, from_number)
        coll = self._db.collection(COLLECTION_NAME)
        doc_ref = coll.document(doc_id)
        now = _now_utc()

        doc = doc_ref.get()
        messages: list[dict[str, Any]] = []
        if doc and doc.exists:
            data = doc.to_dict() or {}
            messages = list(data.get("messages") or [])

        messages.append({
            "role": role,
            "content": content.strip(),
            "timestamp": now,
        })
        if len(messages) > settings.CONVERSATION_MAX_STORED:
            messages = messages[-settings.CONVERSATION_MAX_STORED :]

        doc_ref.set({
            "messages": messages,
            "updated_at": now,
            "clinic_id": clinic_id,
            "from_number": from_number,
        }, merge=True)

    def set_conversation_language(
        self,
        clinic_id: str,
        from_number: str,
        language: str,
    ) -> None:
        """Persist the detected conversation language (e.g. 'es' or 'en') for this user/clinic."""
        if not language:
            return

        coll = self._db.collection(COLLECTION_NAME)
        doc_ref = coll.document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "conversation_language": language,
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def set_patient_name(
        self,
        clinic_id: str,
        from_number: str,
        full_name: str,
    ) -> None:
        """
        Persist the contact/titular full name for this WhatsApp number (saludos).
        NOT the beneficiary when booking for a third party.
        """
        name = (full_name or "").strip()
        if not name:
            return

        parts = name.split()
        first_name = parts[0] if parts else name
        # Normalize to title case (Roberto, María, etc.)
        first_name_norm = first_name[:1].upper() + first_name[1:].lower() if first_name else first_name

        coll = self._db.collection(COLLECTION_NAME)
        doc_ref = coll.document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "patient_name": name,
                "patient_first_name": first_name_norm,
                "name_collection_phase": "known",
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def set_name_collection_phase(
        self,
        clinic_id: str,
        from_number: str,
        phase: str,
    ) -> None:
        """Marca el estado de recolección de nombre: none | asked | known | skipped."""
        p = (phase or "").strip()
        if p not in {"none", "asked", "known", "skipped"}:
            return
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "name_collection_phase": p,
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def set_last_discussed_service(
        self,
        clinic_id: str,
        from_number: str,
        service_id: str,
    ) -> None:
        """Persiste el último servicio consultado (id interno del catálogo)."""
        sid = (service_id or "").strip()
        if not sid:
            return
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "last_discussed_service_id": sid,
                "last_discussed_service_at": now,
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def clear_last_discussed_service(self, clinic_id: str, from_number: str) -> None:
        """Limpia el contexto de servicio consultado."""
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "last_discussed_service_id": "",
                "last_discussed_service_at": None,
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def set_cordales_xray_phase(
        self,
        clinic_id: str,
        from_number: str,
        phase: str,
    ) -> None:
        """Marca el estado del flujo cordales + radiografía panorámica."""
        p = (phase or "").strip()
        if p not in {"none", "asked", "has_panoramic", "needs_at_clinic"}:
            return
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "cordales_xray_phase": p,
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def set_human_transfer_awaiting_summary(
        self,
        clinic_id: str,
        from_number: str,
        *,
        summary: str,
        categories: list[str],
    ) -> None:
        """Marca la conversación en espera de confirmación del resumen antes de notificar al especialista."""
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        cats = [str(c).strip() for c in categories if str(c).strip()]
        doc_ref.set(
            {
                "human_transfer_phase": "awaiting_summary",
                "human_transfer_summary": (summary or "").strip(),
                "human_transfer_categories": cats,
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def update_human_transfer_summary(
        self,
        clinic_id: str,
        from_number: str,
        *,
        summary: str,
    ) -> None:
        """Actualiza el borrador del resumen tras feedback del paciente."""
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "human_transfer_phase": "awaiting_summary",
                "human_transfer_summary": (summary or "").strip(),
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def clear_human_transfer(self, clinic_id: str, from_number: str) -> None:
        """Finaliza el flujo de derivación (campos explícitos para evitar campos huérfanos)."""
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "human_transfer_phase": "none",
                "human_transfer_summary": "",
                "human_transfer_categories": [],
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def set_booking_awaiting_confirm(
        self,
        clinic_id: str,
        from_number: str,
        *,
        pending: dict[str, str],
    ) -> None:
        """Guarda cita propuesta pendiente de confirmación (sí/confirmo) del paciente."""
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "booking_phase": "awaiting_confirm",
                "booking_pending": dict(pending),
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def clear_booking_pending(self, clinic_id: str, from_number: str) -> None:
        """Limpia el borrador de cita pendiente de confirmación."""
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "booking_phase": "none",
                "booking_pending": {},
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def set_beneficiario_edad(self, clinic_id: str, from_number: str, age: int) -> None:
        """Persiste la edad del niño/niña beneficiario detectada en la conversación."""
        try:
            age_val = int(age)
        except (TypeError, ValueError):
            return
        if age_val < 0 or age_val > 18:
            return
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "beneficiario_edad": age_val,
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def clear_beneficiario_edad(self, clinic_id: str, from_number: str) -> None:
        """Limpia la edad del beneficiario tras agendar o al cerrar el flujo pediátrico."""
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "beneficiario_edad": firestore.DELETE_FIELD,
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def set_maxillofacial_awaiting_followup(self, clinic_id: str, from_number: str) -> None:
        """Tras derivación maxilofacial directa: siguiente mensaje del paciente recibe cierre amable."""
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "maxillofacial_transfer_phase": "awaiting_followup",
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def clear_maxillofacial_transfer(self, clinic_id: str, from_number: str) -> None:
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "maxillofacial_transfer_phase": "none",
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def set_emergency_awaiting_choice(self, clinic_id: str, from_number: str) -> None:
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "emergency_phase": "awaiting_choice",
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def set_emergency_appointment_chosen(self, clinic_id: str, from_number: str) -> None:
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "emergency_phase": "appointment_chosen",
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def set_emergency_awaiting_followup(self, clinic_id: str, from_number: str) -> None:
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "emergency_phase": "awaiting_followup",
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def clear_emergency_fork(self, clinic_id: str, from_number: str) -> None:
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "emergency_phase": "none",
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def set_same_day_awaiting_choice(self, clinic_id: str, from_number: str) -> None:
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "same_day_phase": "awaiting_choice",
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def set_same_day_appointment_chosen(self, clinic_id: str, from_number: str) -> None:
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "same_day_phase": "appointment_chosen",
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def set_same_day_awaiting_followup(self, clinic_id: str, from_number: str) -> None:
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "same_day_phase": "awaiting_followup",
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def clear_same_day_fork(self, clinic_id: str, from_number: str) -> None:
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "same_day_phase": "none",
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def bump_confusion_count(self, clinic_id: str, from_number: str) -> int:
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        doc = doc_ref.get()
        current = 0
        if doc and doc.exists:
            raw = (doc.to_dict() or {}).get("confusion_count")
            if isinstance(raw, int) and raw >= 0:
                current = raw
        new_count = current + 1
        now = _now_utc()
        doc_ref.set(
            {
                "confusion_count": new_count,
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )
        return new_count

    def set_confusion_awaiting_menu(
        self,
        clinic_id: str,
        from_number: str,
        *,
        context: str,
        offered_hours: list[str] | None = None,
    ) -> None:
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        payload: dict[str, Any] = {
            "confusion_phase": "awaiting_menu_choice",
            "confusion_context": (context or "general").strip() or "general",
            "confusion_menu_retries": 0,
            "updated_at": now,
            "clinic_id": clinic_id,
            "from_number": from_number,
        }
        if offered_hours:
            payload["confusion_offered_hours"] = list(offered_hours)
        else:
            payload["confusion_offered_hours"] = firestore.DELETE_FIELD
        doc_ref.set(payload, merge=True)

    def bump_confusion_menu_retries(self, clinic_id: str, from_number: str) -> int:
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        doc = doc_ref.get()
        current = 0
        if doc and doc.exists:
            raw = (doc.to_dict() or {}).get("confusion_menu_retries")
            if isinstance(raw, int) and raw >= 0:
                current = raw
        new_count = current + 1
        now = _now_utc()
        doc_ref.set(
            {
                "confusion_menu_retries": new_count,
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )
        return new_count

    def clear_confusion_state(self, clinic_id: str, from_number: str) -> None:
        doc_ref = self._db.collection(COLLECTION_NAME).document(_doc_id(clinic_id, from_number))
        now = _now_utc()
        doc_ref.set(
            {
                "confusion_count": firestore.DELETE_FIELD,
                "confusion_phase": firestore.DELETE_FIELD,
                "confusion_context": firestore.DELETE_FIELD,
                "confusion_menu_retries": firestore.DELETE_FIELD,
                "confusion_offered_hours": firestore.DELETE_FIELD,
                "updated_at": now,
                "clinic_id": clinic_id,
                "from_number": from_number,
            },
            merge=True,
        )

    def try_claim_meta_webhook_wamid(self, wamid: str) -> bool:
        """
        Dedup de notificaciones Meta duplicadas (mismo ``wamid``).

        Retorna ``True`` si esta instancia debe procesar el mensaje (primer ``create`` en Firestore).
        Retorna ``False`` si ya fue procesado (Meta reintentó el webhook).

        Sin ``wamid`` no hay deduplicación (compatibilidad). Si Firestore falla, fail-open: retorna ``True``
        y se registra error en log.
        """
        w = (wamid or "").strip()
        if not w:
            return True
        key = hashlib.sha256(w.encode("utf-8")).hexdigest()
        ref = self._db.collection(META_WEBHOOK_WAMID_DEDUP_COLLECTION).document(key)
        try:
            ref.create({"wamid": w, "claimed_at": firestore.SERVER_TIMESTAMP})
            return True
        except Conflict:
            logger.info("Meta webhook wamid ya procesado (dedup), se omite: %s...", w[:24])
            return False
        except Exception:
            logger.exception("Meta webhook dedup: error Firestore; se procesa el mensaje (fail-open)")
            return True


__all__ = ["ConversationMemoryService", "COLLECTION_NAME", "META_WEBHOOK_WAMID_DEDUP_COLLECTION"]
