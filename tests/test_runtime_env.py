"""Resolución de entorno dev/prod (BigQuery, Firestore, WhatsApp)."""

import os

import pytest

from backend.domain.runtime_env import (
    DEV_BIGQUERY_DATASET,
    PROD_BIGQUERY_DATASET,
    PROD_FIRESTORE_DATABASE_ID,
    effective_bigquery_dataset,
    effective_firestore_database_id,
    is_production_app_env,
    resolve_whatsapp_phone_number_id_for_outbound,
)
from backend.schemas.clinic import ClinicConfig


def test_is_production_app_env():
    assert is_production_app_env("production")
    assert is_production_app_env("prod")
    assert not is_production_app_env("development")


def test_effective_bigquery_dataset_by_app_env(monkeypatch):
    monkeypatch.delenv("BIGQUERY_DATASET", raising=False)
    assert effective_bigquery_dataset(app_env="development") == DEV_BIGQUERY_DATASET
    assert effective_bigquery_dataset(app_env="production") == PROD_BIGQUERY_DATASET


def test_effective_bigquery_dataset_env_override(monkeypatch):
    monkeypatch.setenv("BIGQUERY_DATASET", "custom_ds")
    assert effective_bigquery_dataset(app_env="production") == "custom_ds"


def test_effective_firestore_prod_default(monkeypatch):
    monkeypatch.delenv("FIRESTORE_DATABASE_ID", raising=False)
    assert effective_firestore_database_id(app_env="production") == PROD_FIRESTORE_DATABASE_ID


def test_resolve_whatsapp_outbound_dev_vs_prod():
    cfg = ClinicConfig(
        id="c1",
        name="Test",
        system_prompt="x",
        whatsapp_phone_number_id="prod_id",
        whatsapp_phone_number_id_dev="dev_id",
    )
    assert resolve_whatsapp_phone_number_id_for_outbound(cfg, app_env="development") == "dev_id"
    assert resolve_whatsapp_phone_number_id_for_outbound(cfg, app_env="production") == "prod_id"
