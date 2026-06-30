from __future__ import annotations

import json
import re
import unicodedata
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
        "\n\n[CATÁLOGO DE SERVICIOS – Usa catálogo Y manual de la clínica para precios y alcance; responde antes de escalar a humano.]",
        "Servicios disponibles (id | nombre | precio | evaluación | estado):",
    ]
    if language == "en":
        lines[0] = (
            "\n\n[SERVICES CATALOG – Use catalog AND clinic knowledge base for prices and scope; answer before escalating to a human.]"
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
        lines.append(
            "The clinic knowledge base also describes treatments we offer; if the patient uses a commercial name "
            "(e.g. Biodentine) and the catalog uses another (e.g. pulp capping), treat it as the same service: "
            "answer from the manual and catalog/manual price. NEVER deny a treatment listed in the knowledge base "
            "only because the exact name is missing from this list."
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
        lines.append(
            "El manual de la clínica (base de conocimiento) también describe tratamientos que SÍ ofrecemos; "
            "si el paciente usa un nombre comercial (ej. Biodentine) y en catálogo figura otro (ej. Recubrimiento pulpar), "
            "trátalo como el mismo servicio: responde con la info del manual y el precio del catálogo o manual. "
            "NUNCA niegues un tratamiento que esté en el manual solo porque no encuentras el nombre exacto en esta lista."
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


def catalog_intent_keywords(clinic_id: str) -> list[str]:
    """
    Keywords derivados del catálogo de servicios (nombres, aliases y tokens significativos)
    para el clasificador de intención por reglas. Matching acento-insensible en el clasificador.
    """
    _catalog_stopwords = {
        "dental",
        "dentales",
        "completa",
        "completo",
        "parcial",
        "simple",
        "general",
        "unitario",
        "unitaria",
    }
    keywords: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        term = (raw or "").strip().lower()
        if len(term) < 3:
            return
        base = "".join(
            c for c in unicodedata.normalize("NFD", term) if unicodedata.category(c) != "Mn"
        )
        if base in _catalog_stopwords:
            return
        for variant in (term, base):
            if variant and variant not in seen:
                seen.add(variant)
                keywords.append(variant)

    for svc in _services_for_clinic(clinic_id):
        for field in ("name", "name_en"):
            name = (svc.get(field) or "").strip()
            if name:
                _add(name)
                for token in re.split(r"[^0-9A-Za-zÁÉÍÓÚáéíóúÜüÑñ]+", name):
                    if len(token) >= 4:
                        _add(token)
        for alias in svc.get("aliases") or []:
            _add(str(alias))
        sid = (svc.get("id") or "").strip()
        for token in sid.split("_"):
            if len(token) >= 4:
                _add(token.replace("_", " "))

    return keywords


def catalog_intent_topics(clinic_id: str, *, limit: int = 60) -> list[str]:
    """Nombres legibles del catálogo para el clasificador LLM de intención."""
    topics: list[str] = []
    seen: set[str] = set()
    for svc in _services_for_clinic(clinic_id):
        name = (svc.get("name") or svc.get("name_en") or "").strip()
        if name and name not in seen:
            seen.add(name)
            topics.append(name)
        if len(topics) >= limit:
            break
    return topics
