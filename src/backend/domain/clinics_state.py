from __future__ import annotations

from typing import TYPE_CHECKING, Dict

if TYPE_CHECKING:
    from ..schemas.clinic import ClinicConfig

_clinics_by_id: Dict[str, ClinicConfig] = {}


def init_clinics_by_id(clinics: Dict[str, ClinicConfig]) -> None:
    global _clinics_by_id
    _clinics_by_id = clinics


def get_clinics_by_id() -> Dict[str, ClinicConfig]:
    return _clinics_by_id
