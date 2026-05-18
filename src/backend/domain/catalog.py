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


def _format_services_catalog_for_prompt(services: List[Dict[str, Any]], language: str) -> str:
    """Formatea el catálogo de servicios para inyectarlo en el system prompt (ES/EN)."""
    if not services:
        return ""
    lines = [
        "\n\n[CATÁLOGO DE SERVICIOS – Usa el 'id' cuando agendes una cita o cuando el usuario pregunte por precios.]",
        "Servicios disponibles (id | nombre | precio | estado):",
    ]
    if language == "en":
        lines[0] = "\n\n[SERVICES CATALOG – Use the 'id' when booking an appointment or when the user asks for prices.]"
        lines[1] = "Available services (id | name | price | status):"
    for s in services:
        sid = s.get("id", "")
        name = s.get("name_en", s.get("name", "")) if language == "en" else s.get("name", s.get("name_en", ""))
        price = s.get("price", "")
        currency = s.get("currency", "USD")
        status = s.get("status", "available")
        status_label = "available" if status == "available" else status
        lines.append(f"  - id: {sid} | {name} | {currency} {price} | {status_label}")
    lines.append("Si el usuario pregunta cuánto cuesta algo o por precios, responde con estos datos. Si no indica el tipo de cita al agendar, pregúntale antes de usar la herramienta.")
    if language == "en":
        lines[-1] = "If the user asks how much something costs or for prices, answer using this list. If they don't specify the type of appointment when booking, ask before calling the tool."
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
