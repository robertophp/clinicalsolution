from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..schemas.clinic import ClinicConfig
from ..schemas.clinic_data_files import ClinicBrandFile, ClinicSiteFile
from ..schemas.clinic_policies import ClinicPolicies
from ..services.human_transfer_topics import TransferTopicDefinition, set_transfer_topic_overrides

CLINIC_POLICIES_BY_ID: dict[str, ClinicPolicies] = {}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_transfer_topics_file(path: Path) -> tuple[TransferTopicDefinition, ...]:
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise RuntimeError(f"transfer_topics en {path} debe ser una lista de objetos.")
    out: list[TransferTopicDefinition] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RuntimeError(f"transfer_topics[{i}] en {path} no es un objeto.")
        out.append(
            TransferTopicDefinition(
                key=str(item.get("key", "")).strip(),
                description_es=str(item.get("description_es", "")).strip(),
                description_en=str(item.get("description_en", "")).strip(),
            )
        )
    bad = [t for t in out if not t.key or not t.description_es or not t.description_en]
    if bad:
        raise RuntimeError(f"Entradas incompletas en transfer_topics ({path}).")
    return tuple(out)


def _merge_clinic_config(brand: ClinicBrandFile, site: ClinicSiteFile, policies: ClinicPolicies) -> ClinicConfig:
    if brand.clinic_id != site.clinic_id or brand.clinic_id != policies.clinic_id:
        raise RuntimeError(
            f"Inconsistencia de clinic_id en carpeta {brand.clinic_id!r}: "
            f"brand={brand.clinic_id!r} site={site.clinic_id!r} policies={policies.clinic_id!r}"
        )
    site_dump = site.model_dump(exclude={"clinic_id"})
    return ClinicConfig(
        id=brand.clinic_id,
        name=brand.name,
        assistant_name=brand.assistant_name,
        system_prompt=brand.system_prompt,
        system_prompt_en=brand.system_prompt_en,
        human_transfer_topic_keys=policies.human_transfer_topic_keys,
        **site_dump,
    )


def load_clinic_tree(root: Path) -> dict[str, ClinicConfig]:
    """
    Carga todas las clínicas bajo ``root`` con ``brand.json``, ``site.json`` y ``policies.json``.
    Actualiza ``CLINIC_POLICIES_BY_ID`` y los overrides de temas de derivación humana.
    """
    if not root.is_dir():
        raise FileNotFoundError(f"No existe el directorio de clínicas: {root}")

    clinics: dict[str, ClinicConfig] = {}
    policies_by_id: dict[str, ClinicPolicies] = {}
    transfer_overrides: dict[str, tuple[TransferTopicDefinition, ...]] = {}

    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        brand_path = child / "brand.json"
        site_path = child / "site.json"
        policies_path = child / "policies.json"
        if not brand_path.is_file():
            continue

        try:
            brand = ClinicBrandFile.model_validate(_read_json(brand_path))
        except ValidationError as exc:
            raise RuntimeError(f"brand.json inválido en {child}") from exc

        if not site_path.is_file():
            raise FileNotFoundError(f"Falta site.json para la clínica en {child}")
        if not policies_path.is_file():
            raise FileNotFoundError(f"Falta policies.json para la clínica en {child}")

        try:
            site = ClinicSiteFile.model_validate(_read_json(site_path))
        except ValidationError as exc:
            raise RuntimeError(f"site.json inválido en {child}") from exc

        try:
            policies = ClinicPolicies.model_validate(_read_json(policies_path))
        except ValidationError as exc:
            raise RuntimeError(f"policies.json inválido en {child}") from exc

        cfg = _merge_clinic_config(brand, site, policies)
        if cfg.id in clinics:
            raise RuntimeError(f"clinic_id duplicado: {cfg.id}")
        clinics[cfg.id] = cfg
        policies_by_id[cfg.id] = policies

        rel = (policies.transfer_topics_file or "").strip()
        if rel:
            clinic_root = child.resolve()
            topics_path = (child / rel).resolve()
            try:
                topics_path.relative_to(clinic_root)
            except ValueError as exc:
                raise RuntimeError(
                    f"transfer_topics_file debe estar dentro de la carpeta de la clínica: {rel!r}"
                ) from exc
            if not topics_path.is_file():
                raise FileNotFoundError(f"No existe transfer_topics_file={rel!r} para {cfg.id}")
            transfer_overrides[cfg.id] = _load_transfer_topics_file(topics_path)

    if not clinics:
        raise RuntimeError(f"No se encontraron clínicas en {root} (cada una necesita brand.json, site.json, policies.json).")

    CLINIC_POLICIES_BY_ID.clear()
    CLINIC_POLICIES_BY_ID.update(policies_by_id)
    set_transfer_topic_overrides(transfer_overrides)
    return clinics
