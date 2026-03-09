## Clinica Assistant Agent (Backend)

Backend mínimo para un asistente de IA para clínicas dentales sobre WhatsApp usando:

- **FastAPI** como framework web.
- **Twilio** para el webhook de WhatsApp y respuesta en formato TwiML.
- **Vertex AI Gemini 1.5 Flash** como motor de IA.
- **SQLAlchemy + BigQuery** como base de datos.
- **pydantic-settings** para configuración via variables de entorno / `.env`.

### Estructura principal

- `backend/config.py`: carga configuración con `pydantic-settings` (PROJECT_ID, LOCATION, TWILIO_AUTH).
- `backend/database.py`: configuración de SQLAlchemy con dialecto BigQuery y modelo `Cita`.
- `backend/services/gemini_service.py`: clase `GeminiService` que envuelve Gemini 1.5 Flash.
- `backend/data/clinics_mock.json`: configuración mock de clínicas y sus `system_prompt`.
- `backend/main.py`: webhook `/whatsapp` para Twilio, endpoint `/chat` (JSON) y `/health`.
- `backend/services/conversation_memory.py`: memoria de conversación en Firestore (historial por usuario/clínica, TTL e inactividad).

### Instalación

```bash
python -m venv .venv
source .venv/bin/activate  # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Variables de entorno (.env)

Crear un archivo `.env` en la raíz del proyecto con al menos:

```bash
PROJECT_ID=tu-proyecto-gcp
LOCATION=us-central1
TWILIO_AUTH=token_o_secret_de_twilio_opcional

# Memoria de conversación (Firestore). Opcional; por defecto: 30 min TTL, 5 mensajes de contexto, 20 guardados.
# CONVERSATION_TTL_MINUTES=30
# CONVERSATION_MAX_HISTORY=5
# CONVERSATION_MAX_STORED=20
```

### Ejecutar el servidor

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### Webhook de WhatsApp (Twilio)

- URL: `POST /whatsapp?clinic_id=demo_clinic_1`
- Form data (Twilio):
  - `From`: número del paciente.
  - `Body`: texto del mensaje.

La respuesta será TwiML con un `<Message>` generado por Gemini usando el `system_prompt` configurado para la clínica. El historial de los últimos mensajes del usuario (por número y clínica) se guarda en **Firestore** y se envía a Gemini como contexto; tras X minutos de inactividad solo se usa lo reciente (configurable con `CONVERSATION_TTL_MINUTES`). Necesitas tener Firestore (modo nativo) habilitado en tu proyecto GCP.

# clinicalsolution
Dental Solution using GCP enviorment
