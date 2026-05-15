## Clinica Assistant Agent (Backend)

Backend para un asistente de IA para clínicas dentales sobre WhatsApp usando:

- **FastAPI** como framework web.
- **Twilio** (opcional) para el webhook `POST /whatsapp` y respuesta en formato TwiML.
- **WhatsApp Cloud API (Meta)** para `GET`/`POST /webhooks/whatsapp` (respuesta vía Graph API); guía en [`docs/WHATSAPP_META.md`](docs/WHATSAPP_META.md).
- **Vertex AI Gemini** como motor de IA.
- **SQLAlchemy + BigQuery** como base de datos.
- **pydantic-settings** para configuración vía variables de entorno / `.env`.

Documentación adicional: [**Arquitectura**](docs/ARCHITECTURE.md) · [**Datos por clínica**](docs/CLINIC_DATA.md) · [**Contribuir**](docs/CONTRIBUTING.md)

### Estructura principal

El código del paquete Python vive bajo **`src/backend/`** (layout *src*). El módulo importable sigue siendo `backend` (por ejemplo `uvicorn backend.main:app`).

- `src/backend/config.py`: carga configuración con `pydantic-settings` (PROJECT_ID, LOCATION, TWILIO_AUTH).
- `src/backend/database.py`: configuración de SQLAlchemy con dialecto BigQuery y modelo `Cita`.
- `src/backend/services/gemini_service.py`: clase `GeminiService` que envuelve Gemini 1.5 Flash.
- `src/backend/data/clinics/<clinic_id>/`: configuración por tenant (`brand.json`, `site.json`, `policies.json`); ver [docs/CLINIC_DATA.md](docs/CLINIC_DATA.md).
- `src/backend/data/services_catalog.json`: catálogo de servicios (id, nombre, precio, disponibilidad) para que el asistente informe precios y guarde el tipo de cita en BigQuery.
- `src/backend/main.py`: entry ASGI (`app = create_app()`).
- `src/backend/api/app_factory.py` y `src/backend/api/routers/`: rutas HTTP agrupadas (health, chat, WhatsApp Twilio/Meta, jobs).
- `src/backend/bootstrap.py`: orquestación (clínicas, Gemini, memoria, `_generate_and_persist_reply` y lógica de conversación).
- `src/backend/domain/conversation_prompt.py` y `src/backend/templates/prompt/`: armado del system prompt (referencia de fechas, horarios, catálogo, instrucciones de herramientas vía Jinja2).
- `src/backend/services/conversation_memory.py`: memoria de conversación en Firestore (historial por usuario/clínica, TTL e inactividad).
- `src/backend/services/human_transfer_topics.py` y `src/backend/services/human_transfer_service.py`: derivación a especialista humano por WhatsApp (detección con Gemini, resumen y envío vía Graph API).

### Instalación

Desde la raíz del repositorio (donde están `pyproject.toml` y `requirements.txt`):

```bash
python -m venv .venv
source .venv/bin/activate  # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Eso instala el proyecto en modo **editable** (`src/backend` como paquete `backend`) y las dependencias declaradas en `pyproject.toml`, incluyendo extras de desarrollo (pytest). En producción puedes usar `pip install -e .` sin `[dev]` si no necesitas ejecutar tests en ese entorno.

### Archivo `.env` (no está en el repositorio)

Las variables sensibles y de entorno **no** se versionan. Para desarrollo o despliegue:

1. **Solicita el archivo `.env`** al responsable del proyecto (o al equipo interno) con los valores correctos para tu entorno.
2. Coloca ese archivo en la **raíz** del repositorio (junto a `pyproject.toml`).
3. No lo subas a git: ya está listado en `.gitignore`.

No mantenemos `.env.example` en el repo para evitar confusiones con valores ficticios; las variables que suele necesitar el backend se documentan aquí abajo como referencia (los valores reales van solo en el `.env` que te compartan).

### Variables de entorno (referencia)

Tu archivo `.env` en la raíz debe incluir al menos (nombres ilustrativos; el equipo te entrega los valores reales):

```bash
PROJECT_ID=tu-proyecto-gcp
LOCATION=us-central1
TWILIO_AUTH=token_o_secret_de_twilio_opcional

# Memoria de conversación (Firestore). Opcional; por defecto: 30 min TTL, 5 mensajes de contexto, 20 guardados.
# CONVERSATION_TTL_MINUTES=30
# CONVERSATION_MAX_HISTORY=5
# CONVERSATION_MAX_STORED=20
```

### Secretos y credenciales

- No commitees `key.json`, `.env` ni tokens de Meta/Twilio.
- Si alguna vez se subió un JSON de credenciales a git, **rótalo** en la consola de Google Cloud y genera credenciales nuevas.

### Checklist: agregar una nueva clínica

1. Crea `src/backend/data/clinics/<nuevo_id>/` con `brand.json`, `site.json` y `policies.json` (detalle en [docs/CLINIC_DATA.md](docs/CLINIC_DATA.md)).
2. Añade o filtra entradas en `src/backend/data/services_catalog.json` (`clinic_id` de la clínica o `"*"` para compartidos).
3. Verifica el mapeo de `whatsapp_phone_number_id` → clínica (`domain/clinic_loader.py` + `build_whatsapp_phone_number_id_map`).
4. Prueba un mensaje de WhatsApp (o `/chat`) contra esa `clinic_id`.

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

### BigQuery: tabla de citas

La tabla `clinica_datos.citas` debe tener (además de las columnas que ya uses) la columna **`razon_cita`** (STRING, nullable) y **`status`** (STRING). Valores de `status`:

- **activa**: cita en pie (valor por defecto al crear una cita nueva).
- **cancelada**: el cliente canceló la cita.
- **reagendada**: la cita se movió a otro horario; la cita nueva queda como activa.

#### Columna `transferencia_estado` (derivación a especialista)

El asistente puede marcar conversaciones derivadas a un humano. Los valores usados por el backend son:

- **`pendiente_resumen`**: el paciente tiene un resumen pendiente de confirmación antes de notificar al especialista (se escribe en la **última** fila de `citas` de ese teléfono y `clinica_id`, si existe).
- **`transferido`**: el paciente confirmó el resumen y el mensaje se envió por WhatsApp Cloud API al número configurado en la clínica.

Si el paciente nunca tuvo una fila en `citas`, el flujo de derivación sigue funcionando (Firestore guarda el estado), pero **no** se actualizará BigQuery hasta que exista al menos una cita para ese número.

DDL sugerido:

```sql
ALTER TABLE `clinicalassistant-489223.clinica_datos.citas`
ADD COLUMN IF NOT EXISTS transferencia_estado STRING;
```

(Ajusta el proyecto si usas otro ID de GCP.)

Si la columna `status` no existe en BigQuery:

```sql
ALTER TABLE `tu_proyecto.clinica_datos.citas`
ADD COLUMN IF NOT EXISTS status STRING;
ALTER TABLE `tu_proyecto.clinica_datos.citas`
ADD COLUMN IF NOT EXISTS razon_cita STRING;
```

### Derivación a especialista humano (WhatsApp Cloud)

En `src/backend/data/clinics/<clinic_id>/site.json` y `policies.json`:

- **`specialist_whatsapp`** (en `site.json`): número del especialista en formato E.164 (ej. `+50371234567`) o solo dígitos; debe ser distinto del número del bot.
- **`human_transfer_topic_keys`** (en `policies.json`, opcional): lista de claves para limitar qué temas activan la derivación; si es `null`, se usan todos los del catálogo por defecto en `human_transfer_topics.py`. Opcionalmente define **`transfer_topics_file`** para un catálogo JSON propio en la misma carpeta.

El envío al especialista usa el mismo **`META_WHATSAPP_ACCESS_TOKEN`** y el **`whatsapp_phone_number_id`** de esa clínica (el número de negocio que ya envía al paciente).

Si la columna `transferencia_estado` no existe en BigQuery, las actualizaciones fallarán en log pero el flujo de chat seguirá.

### Catálogo de servicios

En `src/backend/data/services_catalog.json` se define la lista de servicios con `id`, `name`, `name_en`, `price`, `currency`, `status` y `aliases`. El modelo usa este catálogo para: entender qué servicio quiere el usuario (o preguntarle si no está claro), responder preguntas de precios y guardar el `id` del servicio en `razon_cita` al agendar.
