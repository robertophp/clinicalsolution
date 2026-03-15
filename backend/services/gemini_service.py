from __future__ import annotations

import json
import time
from typing import Callable, Iterable, Mapping, Sequence

import vertexai
from vertexai.generative_models import (
    Content,
    FunctionDeclaration,
    GenerationConfig,
    GenerativeModel,
    Part,
    Tool,
)

from ..config import settings


# Herramienta agendar_cita para Gemini (function calling)
# clinica_id se inyecta desde el contexto (webhook); no se pide al usuario.
AGENDAR_CITA_DECLARATION = FunctionDeclaration(
    name="agendar_cita",
    description=(
        "Agenda una cita en la clínica actual (la del contexto de la conversación) cuando el usuario confirme nombre, fecha, hora y tipo de servicio. "
        "Solo llama esta función cuando el paciente haya confirmado explícitamente nombre, fecha, hora y servicio (o razón de la cita). "
        "La fecha y hora deben pasarse en formato normalizado: fecha como YYYY-MM-DD y hora como HH:MM (ej. 14:30). "
        "El parámetro 'servicio' debe ser el ID del servicio según el catálogo de servicios que tienes en contexto (ej. limpieza, revision, extraccion). "
        "Si el usuario pregunta por precios, responde con la información del catálogo sin llamar esta función."
    ),
    parameters={
        "type": "object",
        "properties": {
            "nombre": {"type": "string", "description": "Nombre completo del paciente"},
            "fecha": {"type": "string", "description": "Fecha de la cita en formato YYYY-MM-DD (ej. 2025-03-15). Debes convertir fechas en lenguaje natural a este formato."},
            "hora": {"type": "string", "description": "Hora de la cita en formato HH:MM (ej. 10:00 o 14:30). Debes convertir horas en lenguaje natural a este formato."},
            "servicio": {"type": "string", "description": "ID del servicio según el catálogo de servicios (ej. limpieza, revision, extraccion, obturacion, blanqueamiento, ortodoncia_consulta, emergencia). Debe coincidir con uno de los IDs del catálogo. Si el usuario no ha indicado el tipo de cita, pregúntale antes de agendar."},
        },
        "required": ["nombre", "fecha", "hora", "servicio"],
    },
)

# Cancelar la cita activa del paciente (mismo teléfono y clínica del contexto).
CANCELAR_CITA_DECLARATION = FunctionDeclaration(
    name="cancelar_cita",
    description=(
        "Cancela la cita activa del paciente cuando él lo pida explícitamente (ej. 'quiero cancelar mi cita', 'cancela mi reserva'). "
        "Solo llama esta función cuando el usuario confirme que quiere cancelar. No tiene parámetros: la cita se identifica por el teléfono y la clínica del contexto."
    ),
    parameters={"type": "object", "properties": {}},
)

# Reagendar: marca la cita activa actual como reagendada y crea una nueva con la nueva fecha/hora.
REAGENDAR_CITA_DECLARATION = FunctionDeclaration(
    name="reagendar_cita",
    description=(
        "Reagenda la cita activa del paciente a una nueva fecha y hora cuando él lo pida (ej. 'quiero cambiar mi cita al viernes', 'reagendar para mañana a las 10'). "
        "La cita actual se marca como reagendada y se crea una nueva cita activa. Fecha en YYYY-MM-DD y hora en HH:MM. "
        "Si no indica tipo de servicio, usa el mismo de la cita actual."
    ),
    parameters={
        "type": "object",
        "properties": {
            "fecha": {"type": "string", "description": "Nueva fecha en YYYY-MM-DD"},
            "hora": {"type": "string", "description": "Nueva hora en HH:MM (ej. 10:00 o 14:30)"},
            "servicio": {"type": "string", "description": "ID del servicio (ej. limpieza, revision). Opcional; si no se indica, se conserva el de la cita actual."},
        },
        "required": ["fecha", "hora"],
    },
)

CITAS_TOOLS = Tool(
    function_declarations=[
        AGENDAR_CITA_DECLARATION,
        CANCELAR_CITA_DECLARATION,
        REAGENDAR_CITA_DECLARATION,
    ]
)

# Compatibilidad: herramienta solo agendar (por si se usa en otro flujo).
AGENDAR_CITA_TOOL = Tool(function_declarations=[AGENDAR_CITA_DECLARATION])


class GeminiServiceError(Exception):
    """Errores específicos del servicio Gemini."""


class GeminiService:
    """Wrapper ligero sobre Gemini 1.5 Flash en Vertex AI."""

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        model_name: str = "gemini-2.0-flash-001",
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
            raise GeminiServiceError(
                f"Error generando contenido con Gemini: {type(exc).__name__}: {exc}"
            ) from exc

        text = getattr(response, "text", None)
        if not text:
            raise GeminiServiceError("Gemini devolvió una respuesta vacía.")

        return text.strip()

    def generate_reply_with_tools(
        self,
        system_prompt: str,
        chat_history: Sequence[Mapping[str, str]] | None = None,
        *,
        tool_handler: Callable[[str, dict], dict],
        temperature: float = 0.3,
        max_output_tokens: int = 512,
        max_tool_rounds: int = 3,
    ) -> str:
        """
        Genera respuesta usando herramientas (function calling).
        Si el modelo devuelve function_calls, se invoca tool_handler(name, args) y se reenvía
        el resultado a Gemini para obtener el texto final.
        """
        if self._model is None:
            raise GeminiServiceError("Modelo Gemini no inicializado.")

        history_text = self._format_history(chat_history or [])
        # #region agent log
        try:
            with open("debug-84132f.log", "a", encoding="utf-8") as _f:
                _f.write(json.dumps({"sessionId":"84132f","runId":"post-fix","hypothesisId":"A","location":"gemini_service.py:after_format_history","message":"_format_history ok","data":{"history_len":len(chat_history or [])},"timestamp":round(time.time()*1000)}) + "\n")
        except Exception:
            pass
        # #endregion
        prompt = self._build_prompt(system_prompt=system_prompt, history_text=history_text)
        config = GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

        contents = [Content(role="user", parts=[Part.from_text(prompt)])]
        used_tools = 0

        while used_tools < max_tool_rounds:
            try:
                response = self._model.generate_content(
                    contents,
                    tools=[CITAS_TOOLS],
                    generation_config=config,
                )
            except Exception as exc:
                raise GeminiServiceError(
                    f"Error generando contenido con Gemini: {type(exc).__name__}: {exc}"
                ) from exc

            if not response.candidates:
                raise GeminiServiceError("Gemini devolvió una respuesta vacía.")

            candidate = response.candidates[0]
            fc_list = getattr(candidate, "function_calls", None) or []

            if not fc_list:
                text = getattr(response, "text", None)
                if not text:
                    raise GeminiServiceError("Gemini devolvió una respuesta vacía.")
                return text.strip()

            used_tools += 1
            response_parts = []
            for fc in fc_list:
                name = getattr(fc, "name", None) or ""
                args = dict(getattr(fc, "args", None) or {})
                result = tool_handler(name, args)
                response_parts.append(Part.from_function_response(name=name, response=result))

            contents.append(Content(role="model", parts=candidate.content.parts))
            contents.append(Content(role="user", parts=response_parts))

        raise GeminiServiceError("Se excedió el número máximo de rondas de herramientas.")
    # #region agent log
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
        """
        Construye el prompt: primero System Instruction (instrucciones de la clínica),
        después el historial de mensajes. El orden es fijo para que Gemini priorice las instrucciones.
        """
        parts = [
            "=== System Instruction (instrucciones de la clínica) ===",
            system_prompt.strip(),
            "",
        ]
        if history_text:
            parts.extend([
                "=== Historial de conversación ===",
                history_text,
                "",
            ])
        parts.append(
            "Responde como asistente para una clínica dental, de forma clara y empática, "
            "en el mismo idioma en que te escriba el usuario."
        )
        return "\n".join(parts)


__all__ = ["GeminiService", "GeminiServiceError"]

