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
        Persist the patient's full name and first name for this user/clinic.
        Used so the assistant can greet by first name and avoid asking again.
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
