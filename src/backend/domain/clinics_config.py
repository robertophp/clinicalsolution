from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from pydantic import ValidationError

from ..schemas.clinic import ClinicConfig
from .runtime_env import clinic_whatsapp_phone_number_ids


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
    """
    Mapea Meta ``phone_number_id`` → ``clinic_id``.

    Registra ``whatsapp_phone_number_id`` (producción) y ``whatsapp_phone_number_id_dev``
    (prueba). Cada número se registra en el webhook del servicio Cloud Run correspondiente
    (dev o prod, desplegados por separado desde las ramas ``dev`` y ``main``); este mapa
    solo resuelve a qué clínica pertenece cada ``phone_number_id`` una vez que el evento
    ya llegó al servicio.
    """
    m: Dict[str, str] = {}
    for cid, cfg in clinics.items():
        for pid in clinic_whatsapp_phone_number_ids(cfg):
            if pid in m and m[pid] != cid:
                raise RuntimeError(
                    f"phone_number_id duplicado {pid!r}: clínicas {m[pid]!r} y {cid!r}"
                )
            m[pid] = cid
    return m
