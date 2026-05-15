# Datos por clínica (`data/clinics/`)

Cada tenant vive en una carpeta con el mismo identificador que usarás en `clinic_id` (query Twilio, tests, etc.):

```
src/backend/data/clinics/
  <clinic_id>/
    brand.json      # nombre, asistente, system prompts (tono / rol)
    site.json       # horarios, calendario, WhatsApp Meta, pagos, ubicación
    policies.json   # derivación humana, textos de confirmación de agendado, etc.
    transfer_topics.json   # opcional; ver policies.transfer_topics_file
```

Al arrancar, `domain/clinic_loader.py` valida con Pydantic, fusiona `brand` + `site` en el modelo runtime `ClinicConfig` y guarda `ClinicPolicies` en `CLINIC_POLICIES_BY_ID` (usado al armar el system prompt).

## `brand.json`

| Campo | Descripción |
|--------|-------------|
| `clinic_id` | Debe coincidir con el nombre de la carpeta y con `site` / `policies`. |
| `name` | Nombre comercial de la clínica. |
| `assistant_name` | Cómo se presenta el bot. |
| `system_prompt` | Instrucciones base en español. |
| `system_prompt_en` | Opcional; si falta y el usuario escribe en inglés, el backend añade una nota para responder en inglés. |

## `site.json`

Misma información operativa que antes vivía en el objeto de clínica dentro de `clinics_mock.json`: `opening_hours`, `allowed_intents`, `calendar_id`, `calendar_sync_enabled`, enlaces y textos de ubicación, `whatsapp_phone_number_id` (Meta), `specialist_whatsapp`, `payment_methods`, etc.

## `policies.json`

| Campo | Descripción |
|--------|-------------|
| `clinic_id` | Consistencia con los otros archivos. |
| `human_transfer_topic_keys` | Lista opcional de claves (`quejas`, `especialidades`, …) para filtrar temas de derivación; `null` = todos los del catálogo por defecto en código. |
| `transfer_topics_file` | Nombre de archivo **dentro de la misma carpeta** (p. ej. `transfer_topics.json`) que sustituye por completo el catálogo de temas para esa clínica; sigue aplicándose el filtro por `human_transfer_topic_keys` si está definido. |
| `booking` | Objeto opcional. Hoy se usa `confirmation_example_es` y `confirmation_example_en` para el ejemplo de confirmación en las instrucciones de herramientas (Jinja). Si se omiten, se usan los textos por defecto del backend. |

## Prompts y Jinja2

Los bloques largos de identidad, reglas de horario de cita y herramientas de calendario se renderizan desde `src/backend/templates/prompt/` (`identity_extra.j2`, `schedule_rule.j2`, `citas_tools.j2`). La composición final sigue en `domain/conversation_prompt.py` (fechas de referencia, catálogo, bloque de urgencia/dolor).

## Checklist: nueva clínica

1. Crear `src/backend/data/clinics/<nuevo_id>/` con los tres JSON obligatorios.
2. Añadir servicios en `data/services_catalog.json` para ese `clinic_id` (o `"*"`).
3. Si usas Meta, rellenar `whatsapp_phone_number_id` en `site.json` y documentar el valor en tu `.env` (token, app secret, verify token).
4. Probar `/chat?clinic_id=<nuevo_id>` y, si aplica, el webhook Meta o Twilio.

## Archivo JSON plano legado

`load_clinics_config()` en `domain/clinics_config.py` sigue existiendo para importar un único JSON con `{ "clinics": [ ... ] }` (útil en scripts o migraciones). El servidor en `bootstrap.py` usa exclusivamente el árbol `data/clinics/`.
