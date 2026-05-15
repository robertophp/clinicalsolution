# Arquitectura del backend

## Flujo de una respuesta WhatsApp (resumen)

1. **HTTP**: Meta o Twilio llama a rutas en `src/backend/api/routers/` (`whatsapp_meta.py`, `whatsapp_twilio.py`).
2. **Orquestación**: Las rutas delegan en `bootstrap.py` (por ejemplo `_generate_and_persist_reply`), que une memoria (Firestore), BigQuery opcional, intents y Gemini.
3. **Prompt**: El system instruction efectivo se arma en `domain/conversation_prompt.py` (`build_conversation_system_prompt`): prompt de clínica, fecha de referencia, identidad del asistente, horarios, ubicación, pagos, catálogo de servicios, urgencia/dolor e instrucciones de herramientas. Los bloques largos se renderizan con **Jinja2** desde `templates/prompt/`.
4. **IA**: `services/gemini_service.py` envía el prompt y el historial a Vertex AI con *function calling* (citas, disponibilidad, listar citas, etc.).
5. **Persistencia**: Los handlers en `domain/citas_handlers.py` y `repositories/cita_repository.py` escriben en BigQuery; `services/conversation_memory.py` guarda el hilo en Firestore.

## Dónde tocar qué

| Objetivo | Archivos típicos |
|----------|------------------|
| Nueva clínica o tono por tenant | `data/clinics/<clinic_id>/` (`brand.json`, `site.json`, `policies.json`) — ver [CLINIC_DATA.md](CLINIC_DATA.md) |
| Servicios y precios | `data/services_catalog.json` |
| Texto de horarios / pagos / ubicación en el prompt | `domain/prompt_clinic.py` |
| Plantillas de identidad, reglas de cita y herramientas | `templates/prompt/*.j2` y `domain/conversation_prompt.py` |
| Lógica al guardar/cancelar/reagendar cita | `domain/citas_handlers.py`, `repositories/cita_repository.py` |
| Definición de tools para Gemini | `services/gemini_service.py` |
| Derivación a humano | `services/human_transfer_service.py`, `services/human_transfer_topics.py`, `policies.json` + opcional `transfer_topics.json` |
| Proteger `/chat` y diagnósticos `/health/gcp`, `/health/meta` | Variable `INTERNAL_API_KEY`; dependencia `api/internal_auth.py` |
| Arranque en producción (`APP_ENV=production`) | `api/startup_checks.py` (Meta + API key) |
| Timeout / reintentos Vertex Gemini | `services/gemini_vertex_call.py` + `services/gemini_service.py` (`GEMINI_*` en config) |
| Dedup webhook Meta (`wamid`) | Firestore `meta_webhook_wamid_dedup`; `ConversationMemoryService.try_claim_meta_webhook_wamid` |

## Layout del paquete `backend`

- `api/`: FastAPI, routers.
- `domain/`: reglas de negocio y composición de prompts (sin acoplar a HTTP).
- `services/`: clientes externos (Gemini, WhatsApp, Calendar, Firestore).
- `repositories/`: acceso a datos tabulares (BigQuery).
- `data/`: JSON de configuración y catálogo (versionable con cuidado: sin secretos).
- `templates/prompt/`: plantillas Jinja2 del system prompt.
