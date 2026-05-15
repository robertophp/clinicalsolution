from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from pydantic import ValidationError

from ..schemas.clinic import ClinicConfig


def load_clinics_config(path: Path) -> Dict[str, ClinicConfig]:
    """Load clinic configuration from a JSON file into a dict keyed by clinic_id."""
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el archivo de configuración de clínicas en: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("No se pudo leer o parsear el archivo JSON de clínicas.") from exc

    clinics_raw: List[Dict[str, Any]] = data.get("clinics", [])
    clinics: Dict[str, ClinicConfig] = {}
    for clinic in clinics_raw:
        try:
            cfg = ClinicConfig(**clinic)
        except ValidationError as exc:  # noqa: BLE001
            raise RuntimeError(f"Configuración de clínica inválida: {clinic!r}") from exc
        clinics[cfg.id] = cfg

    if not clinics:
        raise RuntimeError("No se encontraron clínicas configuradas en el archivo JSON.")

    return clinics


def build_whatsapp_phone_number_id_map(clinics: Dict[str, ClinicConfig]) -> Dict[str, str]:
    """Mapea Meta `phone_number_id` → `clinic_id` (solo clínicas con whatsapp_phone_number_id en JSON)."""
    m: Dict[str, str] = {}
    for cid, cfg in clinics.items():
        pid = getattr(cfg, "whatsapp_phone_number_id", None)
        if pid and str(pid).strip():
            m[str(pid).strip()] = cid
    return m
