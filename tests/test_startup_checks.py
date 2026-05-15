"""Validación de arranque en APP_ENV=production."""

from unittest.mock import MagicMock, patch

import pytest

from backend.api.startup_checks import validate_production_settings


def test_production_requires_internal_api_key():
    m = MagicMock()
    m.APP_ENV = "production"
    m.INTERNAL_API_KEY = ""
    m.META_WHATSAPP_ACCESS_TOKEN = ""
    m.META_APP_SECRET = ""
    m.META_WEBHOOK_SKIP_SIGNATURE_VERIFY = False
    with patch("backend.api.startup_checks.settings", m):
        with pytest.raises(RuntimeError, match="INTERNAL_API_KEY"):
            validate_production_settings()


def test_production_with_meta_token_requires_app_secret_and_no_skip():
    m = MagicMock()
    m.APP_ENV = "production"
    m.INTERNAL_API_KEY = "secret-key"
    m.META_WHATSAPP_ACCESS_TOKEN = "meta-token"
    m.META_APP_SECRET = ""
    m.META_WEBHOOK_SKIP_SIGNATURE_VERIFY = False
    with patch("backend.api.startup_checks.settings", m):
        with pytest.raises(RuntimeError, match="META_APP_SECRET"):
            validate_production_settings()

    m2 = MagicMock()
    m2.APP_ENV = "production"
    m2.INTERNAL_API_KEY = "secret-key"
    m2.META_WHATSAPP_ACCESS_TOKEN = "meta-token"
    m2.META_APP_SECRET = "app-secret"
    m2.META_WEBHOOK_SKIP_SIGNATURE_VERIFY = True
    with patch("backend.api.startup_checks.settings", m2):
        with pytest.raises(RuntimeError, match="META_WEBHOOK_SKIP_SIGNATURE_VERIFY"):
            validate_production_settings()


def test_development_skips_strict_checks():
    m = MagicMock()
    m.APP_ENV = "development"
    m.INTERNAL_API_KEY = ""
    m.META_WHATSAPP_ACCESS_TOKEN = "x"
    m.META_APP_SECRET = ""
    m.META_WEBHOOK_SKIP_SIGNATURE_VERIFY = True
    with patch("backend.api.startup_checks.settings", m):
        validate_production_settings()
