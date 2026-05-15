"""Carga de clínicas desde data/clinics/."""

from pathlib import Path

import pytest

from backend.domain.clinic_loader import CLINIC_POLICIES_BY_ID, load_clinic_tree
from backend.services.human_transfer_topics import resolve_transfer_topics_for_clinic


@pytest.fixture(scope="module")
def clinics_root() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "backend" / "data" / "clinics"


def test_load_clinic_tree_demo_clinics(clinics_root: Path) -> None:
    clinics = load_clinic_tree(clinics_root)
    assert "demo_clinic_1" in clinics
    assert "demo_clinic_2" in clinics
    assert clinics["demo_clinic_1"].name == "Clínica Dental Tu Sonrisa"
    assert clinics["demo_clinic_1"].whatsapp_phone_number_id == "1098116840045683"
    assert CLINIC_POLICIES_BY_ID["demo_clinic_1"].human_transfer_topic_keys is None


def test_demo_clinic_2_transfer_topic_filter(clinics_root: Path) -> None:
    load_clinic_tree(clinics_root)
    topics = resolve_transfer_topics_for_clinic("demo_clinic_2", ["quejas"])
    assert len(topics) == 1
    assert topics[0].key == "quejas"
