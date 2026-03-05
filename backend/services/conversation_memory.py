"""
Conversation memory using Firestore.
Stores messages per clinic_id + from_number, with TTL and max history limits.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any

from google.cloud import firestore

from ..config import settings


# Collection name in Firestore
COLLECTION_NAME = "agentmemory"


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
            self._client = firestore.Client(project=self._project_id)
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


__all__ = ["ConversationMemoryService", "COLLECTION_NAME"]
