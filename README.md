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

# Entorno: en production se valida configuración Meta y obligatoriedad de INTERNAL_API_KEY.
# APP_ENV=development

# BigQuery y Firestore (opcional en local; en Cloud Run se pueden omitir y se eligen por APP_ENV)
# BIGQUERY_DATASET=clinica_datos
# FIRESTORE_DATABASE_ID=agentmemory

# API key para POST /chat y GET /health/gcp, /health/meta (Bearer o X-API-Key). En producción es obligatoria.
# INTERNAL_API_KEY=genera_un_valor_largo_aleatorio

# Memoria de conversación (Firestore). Opcional; por defecto: 30 min TTL, 5 mensajes de contexto, 20 guardados.
# CONVERSATION_TTL_MINUTES=30
# CONVERSATION_MAX_HISTORY=5
# CONVERSATION_MAX_STORED=20

# Vertex Gemini: timeout por llamada (s) y máximo de intentos (1er intento + reintentos con backoff).
# GEMINI_GENERATE_TIMEOUT_SECONDS=60
# GEMINI_MAX_GENERATION_ATTEMPTS=3
```

### Entornos STG y PROD (datos y WhatsApp)

El mismo código sirve para staging y producción. **No copies IDs de WhatsApp entre ramas de git**: en cada `site.json` van los dos campos:

| Campo en `site.json` | Uso |
|----------------------|-----|
| `whatsapp_phone_number_id` | Número oficial (PROD, `APP_ENV=production`) |
| `whatsapp_phone_number_id_dev` | Número de prueba Meta (STG / local, `APP_ENV=development`) |

El webhook enruta por el `phone_number_id` que Meta envía en cada mensaje; ambos IDs pueden apuntar a la misma `clinic_id`.

**BigQuery** (tabla siempre `citas`):

| Entorno | Dataset por defecto |
|---------|---------------------|
| STG / desarrollo | `clinica_datos` |
| PROD | `clinica_datos_prod` |

**Firestore**:

| Entorno | Base por defecto |
|---------|------------------|
| STG / desarrollo | `agentmemory` |
| PROD | `agentmemory-prod` |

Si defines `BIGQUERY_DATASET` o `FIRESTORE_DATABASE_ID` en las variables del servicio Cloud Run, esos valores tienen prioridad. La lógica está en `domain/runtime_env.py`.

### Despliegue en Cloud Run (Docker)

La imagen **no** incluye `.env`. Configura secretos y variables en el servicio Run (o en GitHub Actions al desplegar).

#### Checklist STG (`clinicalsolution-stg`)

1. **Artifact Registry**: repositorio Docker `clinicalsolution` en tu región.
2. **GitHub Actions secrets** (Settings → Secrets and variables → Actions), sin Secret Manager por ahora:

   | Secret | Obligatorio |
   |--------|-------------|
   | `GCP_PROJECT_ID` | Sí |
   | `GCP_REGION` | Sí |
   | `GCP_SA_KEY` | Sí (JSON SA para build/deploy) |
   | `META_WHATSAPP_ACCESS_TOKEN` | Sí |
   | `META_APP_SECRET` | Sí |
   | `META_WEBHOOK_VERIFY_TOKEN` | Sí |
   | `GCP_RUN_SERVICE_ACCOUNT` | Recomendado (email SA de Run: Vertex, BQ, Firestore) |
   | `INTERNAL_API_KEY` | No en STG (`APP_ENV=development`; `/chat` abierto si falta) |

3. Push a la rama **`dev`** → workflow `.github/workflows/deploy-stg.yml` crea/actualiza el servicio y la imagen.
4. El workflow fija: `APP_ENV=development`, `BIGQUERY_DATASET=clinica_datos`, `FIRESTORE_DATABASE_ID=agentmemory`.

#### Primer deploy manual (opcional)

```bash
gcloud auth configure-docker us-central1-docker.pkg.dev
docker build -t us-central1-docker.pkg.dev/PROJECT_ID/clinicalsolution/api:local .
docker push us-central1-docker.pkg.dev/PROJECT_ID/clinicalsolution/api:local
gcloud run deploy clinicalsolution-stg \
  --image=us-central1-docker.pkg.dev/PROJECT_ID/clinicalsolution/api:local \
  --region=us-central1 \
  --service-account=TU_SA_RUN@PROJECT_ID.iam.gserviceaccount.com \
  --set-env-vars="APP_ENV=development,PROJECT_ID=PROJECT_ID,LOCATION=us-central1,BIGQUERY_DATASET=clinica_datos,FIRESTORE_DATABASE_ID=agentmemory,META_WHATSAPP_ACCESS_TOKEN=...,META_APP_SECRET=...,META_WEBHOOK_VERIFY_TOKEN=..."
```

#### Webhook Meta en STG

En [Meta for Developers](https://developers.facebook.com) → tu app → WhatsApp → **Configuration** → **Callback URL**:

`https://TU-URL-DE-CLOUD-RUN-STG/webhooks/whatsapp`

Mientras esa URL sea la del servicio **STG**:

- Los mensajes al **número de prueba** (`whatsapp_phone_number_id_dev`) llegan a STG y usan BigQuery/Firestore de desarrollo.
- El **número oficial** solo debe usar la URL del servicio **PROD** cuando estés listo; si el webhook sigue apuntando a STG, los clientes reales hablarían con el entorno de prueba.

Para no afectar clientes: webhook de prod → servicio prod; pruebas solo escribiendo al número de test con webhook en STG (o ngrok en local). Ver [`docs/WHATSAPP_META.md`](docs/WHATSAPP_META.md).

#### PROD (cuando corresponda)

Servicio separado (ej. `clinicalsolution-prod`), rama `main`, `APP_ENV=production`, datasets por defecto `clinica_datos_prod` y `agentmemory-prod`, webhook Meta apuntando a la URL prod.

### Endpoint `/chat` y diagnósticos

- **`POST /chat`**: si defines **`INTERNAL_API_KEY`**, cada petición debe incluir `Authorization: Bearer <INTERNAL_API_KEY>` o `X-API-Key: <INTERNAL_API_KEY>`. Si la variable está vacía o no existe, el endpoint queda abierto (solo conviene en local).
- **`GET /health`**: sigue siendo público (respuesta `OK`).
- **`GET /health/gcp`** y **`GET /health/meta`**: usan la misma API key que `/chat` cuando `INTERNAL_API_KEY` está definida.

Con **`APP_ENV=production`** (o `prod`), al arrancar la app se exige `INTERNAL_API_KEY` no vacío; si además hay **`META_WHATSAPP_ACCESS_TOKEN`**, deben existir **`META_APP_SECRET`** y **`META_WEBHOOK_SKIP_SIGNATURE_VERIFY=false`**.

### Secretos y credenciales

- No commitees `key.json`, `.env` ni tokens de Meta/Twilio.
- Si alguna vez se subió un JSON de credenciales a git, **rótalo** en la consola de Google Cloud y genera credenciales nuevas.

### Checklist: agregar una nueva clínica

1. Crea `src/backend/data/clinics/<nuevo_id>/` con `brand.json`, `site.json` y `policies.json` (detalle en [docs/CLINIC_DATA.md](docs/CLINIC_DATA.md)).
2. Añade o filtra entradas en `src/backend/data/services_catalog.json` (`clinic_id` de la clínica o `"*"` para compartidos).
3. En `site.json`, define `whatsapp_phone_number_id` (prod) y `whatsapp_phone_number_id_dev` (prueba). Verifica el mapa en `GET /health/meta`.
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

### WhatsApp Cloud (Meta): reintentos del webhook

Meta puede enviar el mismo mensaje varias veces. El backend deduplica por **`wamid`** (campo `id` del mensaje) con un documento en Firestore en la colección **`meta_webhook_wamid_dedup`**: el segundo envío recibe **200** sin volver a llamar a Gemini ni a Graph API. Opcional: configura **TTL** sobre esa colección en la consola de GCP para limpiar claves antiguas.

### BigQuery: tabla de citas

En **STG** las citas van a ``clinica_datos.citas``; en **PROD** a ``clinica_datos_prod.citas`` (mismo esquema de columnas).

La tabla `clinica_datos.citas` (y la copia en prod) debe tener (además de las columnas que ya uses) la columna **`razon_cita`** (STRING, nullable) y **`status`** (STRING). Valores de `status`:

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
