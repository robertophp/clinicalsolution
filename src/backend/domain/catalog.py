from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _load_services_catalog(path: Path) -> List[Dict[str, Any]]:
    """Carga el catálogo de servicios desde JSON (id, name, price, status, aliases)."""
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data.get("services", [])
    except (OSError, json.JSONDecodeError):
        return []


try:
    _SERVICES_RAW = _load_services_catalog(_BACKEND_DIR / "data" / "services_catalog.json")
except Exception:  # noqa: BLE001
    _SERVICES_RAW = []


def evaluation_services_for_clinic(clinic_id: str) -> List[Dict[str, Any]]:
    """Servicios del catálogo marcados con ``is_evaluation`` (consulta/evaluación previa)."""
    return [s for s in _services_for_clinic(clinic_id) if s.get("is_evaluation") is True]


def _format_services_catalog_for_prompt(services: List[Dict[str, Any]], language: str) -> str:
    """Formatea el catálogo de servicios para inyectarlo en el system prompt (ES/EN)."""
    if not services:
        return ""
    eval_services = [s for s in services if s.get("is_evaluation") is True]
    lines = [
        "\n\n[CATÁLOGO DE SERVICIOS – Prioridad: responde precios y citas con este catálogo ANTES de escalar a humano.]",
        "Servicios disponibles (id | nombre | precio | evaluación | estado):",
    ]
    if language == "en":
        lines[0] = (
            "\n\n[SERVICES CATALOG – Priority: answer prices and booking from this catalog BEFORE escalating to a human.]"
        )
        lines[1] = "Available services (id | name | price | evaluation | status):"
    for s in services:
        sid = s.get("id", "")
        name = s.get("name_en", s.get("name", "")) if language == "en" else s.get("name", s.get("name_en", ""))
        price = s.get("price", "")
        currency = s.get("currency", "USD")
        status = s.get("status", "available")
        status_label = "available" if status == "available" else status
        eval_flag = "sí" if s.get("is_evaluation") else "no"
        if language == "en":
            eval_flag = "yes" if s.get("is_evaluation") else "no"
        lines.append(f"  - id: {sid} | {name} | {currency} {price} | evaluación={eval_flag} | {status_label}")
    if language == "en":
        lines.append(
            "If the user asks for a price or a service in this list, give the listed price with empathy. "
            "For services marked evaluation=yes, add that the listed price is a reference and the best next step "
            "is to book an evaluation appointment so the doctor can give an exact quote after assessing them. "
            "Invite them to schedule that evaluation by name when appropriate. "
            "If they mention pain or ask for a check-up/review without a specific non-evaluation service, "
            "prioritize offering an evaluation appointment from the evaluation=yes rows. "
            "Do NOT escalate to a human for routine price + pain questions when the service is in this catalog."
        )
    else:
        lines.append(
            "Si el usuario pregunta precio o un servicio de esta lista, indica el precio del catálogo con empatía. "
            "En servicios con evaluación=sí, aclara que ese precio es referencial y lo recomendable es agendar "
            "una cita de evaluación para que la doctora valore el caso y dé un precio exacto. "
            "Invita a agendar esa evaluación por su nombre cuando corresponda. "
            "Si menciona dolor o pide revisión/consulta sin un servicio concreto no evaluación, prioriza ofrecer "
            "cita de evaluación (filas evaluación=sí). "
            "NO derives a humano por preguntas rutinarias de precio + dolor si el servicio está en catálogo."
        )
    if eval_services:
        eval_ids = ", ".join((s.get("id") or "") for s in eval_services[:12])
        if language == "en":
            lines.append(
                f"Evaluation service ids for agendar_cita only (pain / check-up; never show to patient): {eval_ids}"
            )
        else:
            lines.append(
                f"Ids de evaluación solo para agendar_cita (dolor / revisión; nunca mostrar al paciente): {eval_ids}"
            )
    if language == "en":
        lines.append(
            "IMPORTANT: NEVER show the patient internal catalog IDs (e.g. evaluacion_dental, limpieza, evaluacion) "
            "or formats like (id: ...) or 'id: ...'. Speak only using the readable service name (name column). "
            "IDs are ONLY for function calling (servicio parameter in agendar_cita/reagendar_cita), never in chat."
        )
        lines.append(
            "If they don't specify the appointment type when booking, ask before calling the tool."
        )
    else:
        lines.append(
            "IMPORTANTE: NUNCA muestres al paciente IDs internos del catálogo "
            "(ej. evaluacion_dental, limpieza, evaluacion) ni formatos como (id: ...) o «id: ...». "
            "Habla solo con el nombre legible del servicio (columna nombre). "
            "Los IDs van ÚNICAMENTE en function calling (parámetro servicio de agendar_cita/reagendar_cita), nunca en el chat."
        )
        lines.append(
            "Si no indica el tipo de cita al agendar, pregúntale antes de usar la herramienta."
        )
    return "\n".join(lines)


def _services_for_clinic(clinic_id: str) -> List[Dict[str, Any]]:
    """
    Filtra servicios por `clinic_id`.

    Regla:
    - si el servicio tiene `clinic_id == clinic_id` => se incluye
    - si el servicio tiene `clinic_id == "*"` => se considera compartido
    - si el servicio no tiene `clinic_id` (compatibilidad) => se incluye
    """
    out: List[Dict[str, Any]] = []
    for s in _SERVICES_RAW:
        sc = s.get("clinic_id")
        if sc is None or sc == "*" or sc == clinic_id:
            out.append(s)
    return out


def _service_display_label(clinic_id: str, service_id: str, language: str) -> str:
    """Nombre legible del servicio para el paciente: `name` en ES, `name_en` en EN; si no hay match, el id."""
    sid = (service_id or "").strip()
    if not sid:
        return ""
    for s in _services_for_clinic(clinic_id):
        if (s.get("id") or "").strip() != sid:
            continue
        if language == "en":
            return (s.get("name_en") or s.get("name") or sid).strip()
        return (s.get("name") or s.get("name_en") or sid).strip()
    return sid
