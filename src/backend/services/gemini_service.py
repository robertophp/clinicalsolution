from __future__ import annotations

import json
import re
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

# El modelo a veces escribe pseudo-código (p. ej. print(default_api.agendar_cita(...))) en lugar de usar function calling.
_TOOL_CODE_LEAK_RE = re.compile(
    r"print\s*\(|default_api\b|\.agendar_cita\s*\(|agendar_cita\s*\(|"
    r"consultar_disponibilidad\s*\(|consultar_primer_dia_disponible\s*\(|"
    r"listar_mis_citas_proximas\s*\(|"
    r"reagendar_cita\s*\(|cancelar_cita\s*\(|suffix_urgencia\s*=",
    re.IGNORECASE,
)


def reply_looks_like_tool_code_leak(text: str) -> bool:
    """True si el texto parece fugas de herramientas/código hacia el paciente."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_TOOL_CODE_LEAK_RE.search(t))


def _code_leak_retry_instruction(reply_language: str) -> str:
    if (reply_language or "").strip().lower().startswith("en"):
        return (
            "FORMAT ERROR: Your last message looked like source code or internal API names (e.g. print(, "
            "default_api, function names with parentheses). Patients must NEVER see that. "
            "Reply again in plain, natural language only, in the same language as the user. "
            "If you need to book or check availability, use function calling — do not type code."
        )
    return (
        "ERROR DE FORMATO: Tu último mensaje parece código o nombres internos de herramientas (ej. print(, "
        "default_api, agendar_cita(...)). El paciente NUNCA debe ver eso. "
        "Responde de nuevo solo en lenguaje natural claro, en el mismo idioma que el usuario. "
        "Si debías agendar o consultar disponibilidad, usa las herramientas del sistema (function calling), no escribas código."
    )


def _code_leak_fallback_message(reply_language: str) -> str:
    if (reply_language or "").strip().lower().startswith("en"):
        return (
            "Sorry, I couldn't format that reply correctly. Please repeat what you need, "
            "or contact the clinic by phone if it's urgent."
        )
    return (
        "Disculpa, no pude generar bien la respuesta. ¿Puedes repetir lo que necesitas? "
        "Si es urgente, también puedes llamar a la clínica."
    )


# Consulta huecos reales (misma lógica que al agendar) antes de ofrecer horarios al paciente.
CONSULTAR_DISPONIBILIDAD_DECLARATION = FunctionDeclaration(
    name="consultar_disponibilidad",
    description=(
        "Consulta en Google Calendar (o el horario de la clínica si no hay calendario) las horas de inicio "
        "disponibles en punto para citas de 60 minutos en una fecha concreta. "
        "DEBES llamar esta función antes de listar u ofrecer horarios concretos al paciente para ese día "
        "(ej. cuando pregunte '¿a qué horas hay?', '¿qué tienes el lunes?', disponibilidad). "
        "Convierte fechas en lenguaje natural a YYYY-MM-DD usando la fecha de referencia del contexto. "
        "No se ofrecen citas el mismo día: la primera disponibilidad es a partir de mañana (hora de El Salvador, UTC-6). "
        "Si nota o horas vacías indican política de anticipación, explícalo con empatía. "
        "En tu respuesta al usuario solo puedes mencionar horas que aparezcan en el campo horas_disponibles "
        "de la respuesta de esta herramienta; no inventes ni completes la lista por tu cuenta."
    ),
    parameters={
        "type": "object",
        "properties": {
            "fecha": {
                "type": "string",
                "description": "Día a consultar en formato YYYY-MM-DD (ej. 2026-04-20).",
            },
        },
        "required": ["fecha"],
    },
)


CONSULTAR_PRIMER_DIA_DISPONIBLE_DECLARATION = FunctionDeclaration(
    name="consultar_primer_dia_disponible",
    description=(
        "Para dolor o urgencia: encuentra el primer día desde mañana (hora El Salvador, UTC-6) que tenga al menos una hora "
        "de inicio libre para cita de 60 minutos, revisando día por día hasta max_días (por defecto 14). "
        "Salta días en que la clínica está cerrada o sin huecos. Devuelve primeras_tres_horas (hasta 3) para ofrecer al paciente. "
        "Si el paciente ya eligió un día concreto, usa consultar_disponibilidad en su lugar."
    ),
    parameters={
        "type": "object",
        "properties": {
            "max_dias": {
                "type": "integer",
                "description": "Máximo de días calendario a revisar desde mañana (1–30). Por defecto 14.",
            },
        },
        "required": [],
    },
)


LISTAR_MIS_CITAS_PROXIMAS_DECLARATION = FunctionDeclaration(
    name="listar_mis_citas_proximas",
    description=(
        "Lista las citas **activas** del paciente asociadas al número de WhatsApp de esta conversación y a la clínica del contexto, "
        "desde el momento actual en hora de El Salvador (UTC-6) en adelante (incluye el día de hoy si la hora de la cita aún no pasó). "
        "Úsala cuando pregunte por sus citas, reservas, 'cuándo tengo cita', 'a qué hora es mi cita', etc. "
        "No tiene parámetros: teléfono y clínica vienen del sistema. "
        "La respuesta trae un arreglo citas con fecha (YYYY-MM-DD), hora (HH:MM) y servicio (nombre); resume al paciente cada cita con fecha, hora y tipo de servicio. "
        "Si citas viene vacío, di claramente que no hay citas próximas registradas."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)


# Herramienta agendar_cita para Gemini (function calling)
# clinica_id se inyecta desde el contexto (webhook); no se pide al usuario.
AGENDAR_CITA_DECLARATION = FunctionDeclaration(
    name="agendar_cita",
    description=(
        "Agenda una cita en la clínica actual (la del contexto de la conversación) cuando el usuario confirme nombre, fecha, hora y tipo de servicio. "
        "La hora en 'hora' debe ser la que el paciente eligió para ESTA reserva (HH:00). "
        "NO reutilices la hora de otra cita mencionada en el historial (ej. una revisión a las 08:00) para una cita nueva de otro servicio salvo que el usuario diga explícitamente que quiere la misma hora. "
        "Si solo confirmaste el día ('¿te parece bien ese día?' y responde sí) pero aún no hay hora concreta para esta reserva, NO llames esta función todavía: "
        "primero consultar_disponibilidad(fecha) y ofrece horas_disponibles, o pregunta «¿a qué hora?», y solo entonces llama agendar_cita con esa hora. "
        "NO uses servicio=evaluacion si el paciente solo dio día/hora o nunca eligió servicio: pregunta qué id del catálogo quiere y, si sugieres evaluación, confirma que acepta evaluacion. "
        "Usa evaluacion solo con dolor/urgencia ya descritos (bloque del sistema) o si pidió explícitamente evaluación y lo confirmó. "
        "Cuando ya resumiste en el chat la reserva completa (servicio, fecha y hora) y pediste confirmación explícita con **sí** o **confirmo** para guardar en el sistema, "
        "y el paciente responde con sí / ok / claro / listo / vale / confirmo / de acuerdo (aceptación clara), llama esta función en ese mismo turno con "
        "nombre, fecha YYYY-MM-DD, hora HH:00 y servicio (id del catálogo). "
        "Un mensaje solo de gracias, «muy amable» o cortesía sin sí/confirmo NO cuenta como confirmación: vuelve a pedir sí o confirmo; no llames la función. "
        "NUNCA digas al paciente que la cita quedó guardada o agendada en el sistema sin haber llamado antes a esta función y recibido su resultado. "
        "Nunca para el mismo día: la cita debe ser a partir de mañana en hora de El Salvador (UTC-6), no hoy. "
        "La fecha y hora deben pasarse en formato normalizado: fecha como YYYY-MM-DD y hora como HH:00 (solo horas en punto, citas de 60 minutos). "
        "El parámetro 'servicio' debe ser el ID del servicio según el catálogo de servicios que tienes en contexto (ej. limpieza, revision, extraccion). "
        "Opcional suffix_urgencia (solo con servicio=evaluacion en flujos de dolor): dolor_post_cita o dolor_intenso, para el título en Google Calendar. "
        "Si el usuario pregunta por precios, responde con la información del catálogo sin llamar esta función."
    ),
    parameters={
        "type": "object",
        "properties": {
            "nombre": {"type": "string", "description": "Nombre completo del paciente"},
            "fecha": {"type": "string", "description": "Fecha de la cita en formato YYYY-MM-DD (ej. 2025-03-15). Debes convertir fechas en lenguaje natural a este formato."},
            "hora": {
                "type": "string",
                "description": "Hora de inicio de ESTA cita en HH:00 (ej. 10:00 o 14:00). Debe ser una hora que el paciente haya elegido o confirmado para esta reserva; no asumas ni copies la hora de otra cita del chat salvo petición explícita del paciente.",
            },
            "servicio": {"type": "string", "description": "ID del servicio según el catálogo (ej. limpieza, revision, extraccion, evaluacion). Debe coincidir con un id del catálogo. No uses evaluacion salvo dolor/urgencia ya descritos o evaluación pedida y confirmada por el paciente; si solo dio día/hora, pregunta primero qué servicio quiere."},
            "suffix_urgencia": {
                "type": "string",
                "description": "Opcional. Solo con servicio=evaluacion en urgencia/dolor: dolor_post_cita (dolor posprocedimiento o tras visita) o dolor_intenso (dolor fuerte sin atarlo a procedimiento reciente). Omítelo en citas normales.",
            },
        },
        "required": ["nombre", "fecha", "hora", "servicio"],
    },
)

# Cancelar la cita activa del paciente (mismo teléfono y clínica del contexto).
CANCELAR_CITA_DECLARATION = FunctionDeclaration(
    name="cancelar_cita",
    description=(
        "Cancela la cita activa del paciente cuando él lo pida explícitamente (ej. 'quiero cancelar mi cita', 'cancela mi reserva'). "
        "Solo llama esta función cuando el usuario confirme que quiere cancelar. No tiene parámetros: la cita se identifica por el teléfono y la clínica del contexto. "
        "No afirmes que la cita quedó cancelada hasta haber llamado esta función y recibido su resultado."
    ),
    parameters={"type": "object", "properties": {}},
)

# Reagendar: marca la cita activa actual como reagendada y crea una nueva con la nueva fecha/hora.
REAGENDAR_CITA_DECLARATION = FunctionDeclaration(
    name="reagendar_cita",
    description=(
        "Reagenda la cita activa del paciente a una nueva fecha y hora cuando él lo pida (ej. 'quiero cambiar mi cita al viernes', 'reagendar para mañana a las 10'). "
        "La nueva fecha no puede ser hoy: a partir de mañana en hora de El Salvador (UTC-6). "
        "La cita actual se marca como reagendada y se crea una nueva cita activa. Fecha en YYYY-MM-DD y hora en HH:00 (solo horas en punto de 60 minutos). "
        "Si no indica tipo de servicio, usa el mismo de la cita actual. "
        "Cuando en el chat ya resumiste la nueva fecha, hora y servicio (si cambia) y pediste **sí** o **confirmo** para guardar el cambio en el sistema, "
        "y el paciente responde con aceptación explícita (sí, confirmo, ok, claro, listo, vale, de acuerdo), llama esta función en ese mismo turno. "
        "Gracias o cortesía sola sin sí/confirmo no basta: vuelve a pedir confirmación; no afirmes que ya quedó reagendada. "
        "No afirmes que la cita quedó reagendada hasta haber llamado esta función y recibido su resultado."
    ),
    parameters={
        "type": "object",
        "properties": {
            "fecha": {"type": "string", "description": "Nueva fecha en YYYY-MM-DD"},
            "hora": {"type": "string", "description": "Nueva hora en HH:00 (ej. 10:00 o 14:00), únicamente horas en punto."},
            "servicio": {"type": "string", "description": "ID del servicio (ej. limpieza, revision). Opcional; si no se indica, se conserva el de la cita actual."},
            "suffix_urgencia": {
                "type": "string",
                "description": "Opcional. Solo con servicio=evaluacion en urgencia/dolor: dolor_post_cita o dolor_intenso. Omítelo en reagendos normales.",
            },
        },
        "required": ["fecha", "hora"],
    },
)

CITAS_TOOLS = Tool(
    function_declarations=[
        CONSULTAR_DISPONIBILIDAD_DECLARATION,
        CONSULTAR_PRIMER_DIA_DISPONIBLE_DECLARATION,
        LISTAR_MIS_CITAS_PROXIMAS_DECLARATION,
        AGENDAR_CITA_DECLARATION,
        CANCELAR_CITA_DECLARATION,
        REAGENDAR_CITA_DECLARATION,
    ]
)

# Herramientas cuyo dict con clave "mensaje" se devuelve tal cual al paciente (éxito o error),
# sin segunda pasada por el modelo, para no inventar confirmaciones ni suavizar errores.
CITAS_MUTATION_TOOL_NAMES = frozenset({"agendar_cita", "reagendar_cita", "cancelar_cita"})

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
        reply_language: str = "es",
        temperature: float = 0.3,
        max_output_tokens: int = 512,
        max_tool_rounds: int = 3,
    ) -> str:
        """
        Genera respuesta usando herramientas (function calling).

        Flujo:
        - Gemini puede devolver llamadas a funciones (function_calls).
        - Para cada llamada se ejecuta tool_handler(name, args).
        - Para agendar_cita, reagendar_cita y cancelar_cita: si el dict incluye 'mensaje',
          se devuelve ese texto al usuario tal cual (con o sin 'error'), sin segunda pasada
          por el modelo, para que no se inventen confirmaciones ni se contradigan errores.
        - Para el resto de herramientas: si hay 'mensaje' y no hay 'error', mismo retorno directo;
          en caso contrario el resultado se reenvía a Gemini.
        - reply_language: 'es' o 'en' para mensajes de reintento/fallback si el modelo devuelve texto con fugas de código.
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
        code_leak_retries = 0
        max_code_leak_retries = 1

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
                out = text.strip()
                if reply_looks_like_tool_code_leak(out) and code_leak_retries < max_code_leak_retries:
                    code_leak_retries += 1
                    contents.append(Content(role="model", parts=candidate.content.parts))
                    contents.append(
                        Content(
                            role="user",
                            parts=[Part.from_text(_code_leak_retry_instruction(reply_language))],
                        )
                    )
                    continue
                if reply_looks_like_tool_code_leak(out):
                    return _code_leak_fallback_message(reply_language)
                return out

            used_tools += 1
            response_parts = []
            for fc in fc_list:
                name = getattr(fc, "name", None) or ""
                args = dict(getattr(fc, "args", None) or {})
                result = tool_handler(name, args)
                # Citas mutantes: siempre el texto del backend al paciente (éxito o error),
                # sin re-delegar en el modelo (evita "quedó agendada" cuando falló BQ, etc.).
                if isinstance(result, dict) and "mensaje" in result and name in CITAS_MUTATION_TOOL_NAMES:
                    mensaje = str(result["mensaje"]).strip()
                    if mensaje:
                        if reply_looks_like_tool_code_leak(mensaje):
                            return _code_leak_fallback_message(reply_language)
                        return mensaje

                if isinstance(result, dict) and "mensaje" in result and not result.get("error"):
                    mensaje = str(result["mensaje"]).strip()
                    if mensaje:
                        if reply_looks_like_tool_code_leak(mensaje):
                            return _code_leak_fallback_message(reply_language)
                        return mensaje

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
        parts.append(
            "Nunca incluyas en tu respuesta al paciente código de programación, llamadas tipo print(...), "
            "default_api, ni nombres de funciones internas con paréntesis (agendar_cita, consultar_disponibilidad, listar_mis_citas_proximas, etc.). "
            "Para agendar o consultar horarios debes usar las herramientas del sistema (function calling), no texto que parezca código."
        )
        return "\n".join(parts)


__all__ = [
    "AGENDAR_CITA_DECLARATION",
    "AGENDAR_CITA_TOOL",
    "CITAS_MUTATION_TOOL_NAMES",
    "CITAS_TOOLS",
    "LISTAR_MIS_CITAS_PROXIMAS_DECLARATION",
    "GeminiService",
    "GeminiServiceError",
    "reply_looks_like_tool_code_leak",
]

