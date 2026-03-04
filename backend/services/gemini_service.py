from __future__ import annotations

from typing import Iterable, Mapping, Sequence

import vertexai
from vertexai.generative_models import GenerationConfig, GenerativeModel

from ..config import settings


class GeminiServiceError(Exception):
    """Errores específicos del servicio Gemini."""


class GeminiService:
    """Wrapper ligero sobre Gemini 1.5 Flash en Vertex AI."""

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        model_name: str = "gemini-1.5-flash",
    ) -> None:
        self._project_id = project_id or settings.PROJECT_ID
        self._location = location or settings.LOCATION
        self._model_name = model_name
        self._model: GenerativeModel | None = None

        self._init_vertex_ai()

    def _init_vertex_ai(self) -> None:
        """Inicializa Vertex AI y el modelo Gemini."""
        try:
            vertexai.init(project=self._project_id, location=self._location)
            self._model = GenerativeModel(self._model_name)
        except Exception as exc:  # noqa: BLE001
            raise GeminiServiceError("Error inicializando Vertex AI / Gemini.") from exc

    def generate_reply(
        self,
        system_prompt: str,
        chat_history: Sequence[Mapping[str, str]] | None = None,
        *,
        temperature: float = 0.3,
        max_output_tokens: int = 512,
    ) -> str:
        """Genera una respuesta textual a partir del prompt del sistema y el historial."""
        if self._model is None:
            raise GeminiServiceError("Modelo Gemini no inicializado.")

        history_text = self._format_history(chat_history or [])
        prompt = self._build_prompt(system_prompt=system_prompt, history_text=history_text)

        try:
            config = GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
            response = self._model.generate_content(prompt, generation_config=config)
        except Exception as exc:  # noqa: BLE001
            raise GeminiServiceError("Error generando contenido con Gemini.") from exc

        text = getattr(response, "text", None)
        if not text:
            raise GeminiServiceError("Gemini devolvió una respuesta vacía.")

        return text.strip()

    @staticmethod
    def _format_history(chat_history: Iterable[Mapping[str, str]]) -> str:
        """Convierte el historial de chat en texto plano estructurado."""
        lines: list[str] = []
        for message in chat_history:
            role = message.get("role", "user")
            content = message.get("content", "").strip()
            if not content:
                continue
            prefix = "Usuario" if role == "user" else "Asistente"
            lines.append(f"{prefix}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _build_prompt(system_prompt: str, history_text: str) -> str:
        """Construye un prompt único para enviar a Gemini."""
        base = f"Sistema (instrucciones):\n{system_prompt.strip()}\n"
        if history_text:
            base += f"\nHistorial de conversación:\n{history_text}\n"
        base += "\nResponde como asistente para una clínica dental, en español, de forma clara y empática."
        return base


__all__ = ["GeminiService", "GeminiServiceError"]

