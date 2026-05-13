"""Entry ASGI: instancia FastAPI creada por `create_app()`."""

from __future__ import annotations

from .api.app_factory import create_app

app = create_app()
