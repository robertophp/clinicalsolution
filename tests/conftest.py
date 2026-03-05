"""
Pytest configuration. Sets minimal env so the app can be imported without a real .env.
Gemini is mocked in tests, so GCP credentials are not used.
"""
import os

# Allow tests to run without .env (app still requires PROJECT_ID / LOCATION at import)
os.environ.setdefault("PROJECT_ID", "test-project")
os.environ.setdefault("LOCATION", "us-central1")
