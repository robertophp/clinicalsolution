# Contribuir

## Configuración local

- Clona el repo e instala dependencias: `pip install -r requirements.txt` (modo editable según `pyproject.toml`).
- **Archivo `.env`**: no está en el repositorio. Solicita al responsable del proyecto el archivo `.env` con las variables necesarias (GCP, Twilio/Meta, etc.) y colócalo en la **raíz** del proyecto. No subas `.env` a git.
- Credenciales de Google: para ejecutar tests o el servidor contra BigQuery/Calendar hace falta **Application Default Credentials** (por ejemplo `GOOGLE_APPLICATION_CREDENTIALS` apuntando a un JSON de cuenta de servicio, o `gcloud auth application-default login`).

## Tests

```bash
pytest -q
```

Varios tests importan módulos que cargan `backend.database` (motor BigQuery). Sin credenciales ADC válidas, la recolección de tests puede fallar con `DefaultCredentialsError`.

## Convenciones

- No commitear secretos (`key.json`, `.env`, tokens).
- Cambios de comportamiento del asistente: revisar `domain/conversation_prompt.py`, las plantillas en `templates/prompt/`, los datos en `data/clinics/` (ver [CLINIC_DATA.md](CLINIC_DATA.md)) y, si aplica, las descripciones de tools en `gemini_service.py`.
- Mantener mensajes al paciente en el idioma correcto (ES/EN) según el flujo existente.
