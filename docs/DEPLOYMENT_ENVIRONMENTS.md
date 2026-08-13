# Estado: separación de entornos dev / prod

> Este archivo se actualiza a medida que se avanza. Si retomas este trabajo en una sesión
> nueva, léelo primero — reemplaza tener que re-explicar el contexto desde cero.

## Por qué existe esto

Hasta agosto 2026, un solo servicio de Cloud Run (`clinicalsolution-stg`, desplegado desde la
rama `dev`) atendía a los pacientes reales de la clínica — no había separación real entre
"staging" y "producción", pese a que el código (`runtime_env.py`) ya traía lógica para
diferenciar por `APP_ENV`. Se decidió pasar a dos servicios Cloud Run separados en el mismo
proyecto GCP, cada uno con su propio dataset de BigQuery y su propia base de Firestore.

## Arquitectura objetivo

| | dev (actual) | prod (nuevo) |
|---|---|---|
| Rama que dispara el deploy | `dev` | `main` |
| Workflow | `.github/workflows/deploy-stg.yml` | `.github/workflows/deploy-prod.yml` |
| Servicio Cloud Run | `clinicalsolution-stg` | `clinicalsolution-prod` |
| `APP_ENV` | `development` | `production` |
| Dataset BigQuery | `clinica_datos` | `cita_datos_prod` |
| Firestore database | `agentmemory` | `agentmemory-prod` |
| GitHub Environment (secretos) | `dev` | `production` |
| Número de WhatsApp | número de prueba | número real de pacientes |

Decisiones ya tomadas (no volver a preguntar):
- El dataset de prod se llama `cita_datos_prod` (no `clinica_datos_prod`, que es el default
  del código — se fija por variable de entorno explícita en `deploy-prod.yml`, ya está así).
- **No se migran** las citas existentes de `clinica_datos` hacia `cita_datos_prod`. El dataset
  de prod arranca vacío. Implicación: cualquier cita real activa/futura que exista en
  `clinica_datos` al momento del corte del webhook queda invisible para el asistente en prod.
  Ver checklist de corte más abajo.
- Secretos de GitHub organizados con **Environments** (`dev` y `production`), no con prefijos
  sueltos en secretos de repo.
- El flujo de trabajo diario es: cambios en `infra` (o la rama de trabajo activa) → push a
  `dev` (valida funcionalmente) → una vez conforme, se promueve `dev` → `main` (activa prod).
- El corte del número real de WhatsApp es un paso **manual en Meta Developer Console**
  (reapuntar la URL del webhook): un WABA/App de Meta solo tiene una URL de webhook activa a
  la vez, no es algo que resuelva el código.
- **Corrección importante (confirmada en vivo, 12-ago-2026):** el webhook de Meta se configura
  en "Configure webhooks" a nivel de App/WABA, **no por número individual**. Esto significa que
  dev y prod **nunca reciben tráfico de WhatsApp al mismo tiempo** — mover la URL mueve todos
  los números registrados en esa App juntos (el de prueba y el real). El plan original de
  "número de prueba fijo en dev, número real fijo en prod, ambos simultáneos" no es posible sin
  una segunda App de Meta dedicada a dev (no se hizo — ver "Flujo de trabajo" abajo).

## Incidente resuelto: `cita_datos_prod` no existía

Durante el dry run con el número de prueba (webhook apuntado a `clinicalsolution-prod`), agendar
una cita falló con `Not found: Dataset clinicalassistant-489223:cita_datos_prod was not found in
location US`. Se verificó directo contra la API de BigQuery: el dataset **nunca se había creado**
(a diferencia de lo asumido antes) — solo existía `clinica_datos` (location `US`, tablas `citas`
18 columnas y `mensajes` 5 columnas).

Se creó `cita_datos_prod` en location `US` (misma región que `clinica_datos`, para evitar
ambigüedad ya que la connection string de `database.py` no especifica location explícita), y se
replicaron ambas tablas: `citas` vía `Base.metadata.create_all(engine)` (mismo modelo SQLAlchemy
que usa el código, garantiza que el esquema coincide) y `mensajes` vía el script existente
`scripts/create_bigquery_mensajes_table.py` (ya era idempotente y respeta `BIGQUERY_DATASET`).
Verificado: ambos datasets tienen ahora el mismo esquema (18 y 5 columnas respectivamente).

## Cambios de código ya aplicados (rama `feature/infra`, sin commitear a la fecha)

- `deploy-stg.yml`: se agregó job `test` (pytest) antes de `deploy` (`needs: test`), y
  `environment: dev` en ambos jobs. Sin cambios de trigger, nombre de servicio ni variables.
- `deploy-prod.yml` (nuevo): mismo patrón, dispara con push a `main`, despliega
  `clinicalsolution-prod` con `APP_ENV=production` y los datasets/DB de prod.
- `domain/clinics_config.py`: comentario actualizado (ya no dice "sin cambiar ramas de git").
- Fuera de este tema pero en la misma rama: fix de llamadas bloqueantes en los webhooks
  (`run_in_threadpool`) y lock anti-doble-reserva (`try_lock_cita_slot` /
  `release_cita_slot` en `conversation_memory.py`, usado en `citas_handlers.py`). Ver commits
  de esta rama para detalle — no repetido aquí para no duplicar.

## Secretos: qué va en repo y qué va en cada Environment

Ya tienes 8 secretos a nivel de repo (`GCP_PROJECT_ID`, `GCP_REGION`, `GCP_SA_KEY`,
`GCP_RUN_SERVICE_ACCOUNT`, `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`,
`META_WHATSAPP_ACCESS_TOKEN`, `DASHBOARD_SESSION_SECRET`). Análisis: son el mismo proyecto GCP
y el mismo WABA/App de Meta para ambos ambientes, así que **la mayoría no necesita duplicarse**
— un secreto de repo se sigue leyendo igual desde un job con `environment:` aunque no exista
un secreto con ese nombre dentro del Environment (el de Environment solo gana si existe con el
mismo nombre). Solo hace falta valor **distinto** por ambiente para lo que es puramente interno
a cada servicio:

- `INTERNAL_API_KEY` — protege `/chat` y diagnósticos de esa instancia puntual. Hoy no existe
  como secreto en ningún lado (ni repo ni Environment) — hay que crearlo.
- `DASHBOARD_SESSION_SECRET` — ya existe a nivel de repo; puedes dejarlo ahí (mismo valor para
  ambos) o darle un valor distinto en cada Environment si quieres aislar sesiones del dashboard.

## Checklist operativo pendiente (lo hace el usuario)

- [x] Crear GitHub Environments `dev` y `production` (Settings → Environments).
- [x] Dentro del Environment `dev`: secreto `INTERNAL_API_KEY` (valor de prueba).
- [x] Dentro del Environment `production`: secreto `INTERNAL_API_KEY` (valor real, distinto).
- [ ] El resto de los 8 secretos existentes se quedan como están a nivel de repo — no hace
      falta copiarlos dentro de cada Environment (ya confirmado, sin acción pendiente).
- [x] Commit + PR + merge de `feature/infra` → `dev`. `deploy-stg.yml` corrió el job `test` y
      desplegó bien sobre `clinicalsolution-stg`. Validado en vivo: cita de prueba agendada,
      visible en BigQuery, Google Calendar y el dashboard — el fix async y el lock
      anti-doble-reserva ya están corriendo en el servicio real.
- [x] Promover `dev` → `main` (PR #22). `deploy-prod.yml` corrió y creó `clinicalsolution-prod`.
      URL: `https://clinicalsolution-prod-751868423989.us-central1.run.app`
- [x] Verificar arranque limpio del nuevo servicio: `/health` → OK, `/health/meta` → Meta
      configurado y ambos `phone_number_id` de `demo_clinic_1` mapeados, `/health/gcp` →
      `firestore: ok`, `gemini: ok`. `clinicalsolution-prod` arranca limpio.
- [x] Corte: el webhook de Meta (el único de la App, ver nota arriba) quedó apuntando a
      `clinicalsolution-prod` — confirmado que el número real ya responde desde ahí.
- [x] Revisado `clinica_datos.citas` por citas activas con fecha futura (después del corte, no
      antes — no se alcanzó a hacer en orden). Resultado: 1 fila (Roberto Menjivar,
      whatsapp:+50374351282, 2026-08-13 16:00, demo_clinic_1) — confirmada como cita de prueba
      propia, no de un paciente real. Decisión: se deja como está en `clinica_datos`, no se migra.
- [x] Verificado con mensaje real (número real) que la respuesta viene de `clinicalsolution-prod`.
- [x] Dashboard de métricas apuntando a prod: el código ya lee el dataset/Firestore por
      ambiente sin hardcodear nada (`metrics_repository.py` usa `effective_dataset()`), así que
      no hizo falta cambiar código. Lo que sí faltaba: la colección `dashboard_users` vive en
      Firestore, y estaba vacía en `agentmemory-prod` (0 usuarios, vs. 1 — `stephanie.palacios`,
      `demo_clinic_1` — en `agentmemory` dev). Se creó el mismo usuario en prod con
      `scripts/create_dashboard_user.py` (`FIRESTORE_DATABASE_ID=agentmemory-prod`). Confirmado:
      login funciona en `https://clinicalsolution-prod-751868423989.us-central1.run.app/dashboard`
      y los datos mostrados corresponden a prod.
- `clinicalsolution-stg` sigue viva pero **ya no recibe tráfico de WhatsApp** (el único webhook
  de la App apunta a prod). Sigue siendo útil como red de seguridad vía rollback de Cloud Run
  (revertir a la revisión anterior sin tocar Meta) y como target de pruebas por `/chat` — no se
  apaga ni se borra.

## Flujo de trabajo (post-corte)

Como dev y prod no pueden recibir WhatsApp a la vez (un solo webhook por App), las pruebas por
WhatsApp real dejan de ser el camino del día a día. Flujo elegido:

1. Cambios de código en una rama de trabajo → merge a `dev` → `deploy-stg.yml` corre tests y
   despliega sobre `clinicalsolution-stg` (sin afectar a pacientes reales, porque ese servicio
   ya no tiene ningún número de WhatsApp apuntándole).
2. Probar la conversación **sin pasar por WhatsApp**, contra `clinicalsolution-stg`, con el
   endpoint que ya existe para esto en el código (`api/routers/chat.py`):

   ```bash
   curl -X POST "https://clinicalsolution-stg-751868423989.us-central1.run.app/chat?clinic_id=demo_clinic_1" \
     -H "Authorization: Bearer <INTERNAL_API_KEY de dev>" \
     -H "Content-Type: application/json" \
     -d '{"from_number": "+50370000000", "body": "Hola, quiero agendar una cita"}'
   ```

   Responde JSON `{"reply": "..."}` — mismo `_generate_and_persist_reply` que usa WhatsApp,
   mismo Firestore/BigQuery de dev (`clinica_datos` / `agentmemory`), cero riesgo para prod.
3. Conforme con las pruebas → merge `dev` → `main` → `deploy-prod.yml` corre tests y despliega
   una revisión nueva sobre `clinicalsolution-prod` (misma URL de siempre — el webhook de Meta
   no se vuelve a tocar en cada deploy, solo cambia la revisión detrás de esa URL).
4. Si algo sale mal después de un deploy a prod: rollback de Cloud Run a la revisión anterior
   (`gcloud run services update-traffic clinicalsolution-prod --to-revisions=REVISION_ANTERIOR=100`),
   sin tocar Meta Developer Console para nada.

Pendiente si más adelante se quiere volver a probar con WhatsApp real antes de cada promoción a
`main` (no bloqueante, evaluar solo si hace falta): crear una segunda App de Meta dedicada al
número de prueba, con su propio webhook fijo hacia `clinicalsolution-stg`, para que dev y prod
puedan convivir con WhatsApp real de forma simultánea y permanente.

## Hallazgos de la auditoría original — estado

- ✅ Doble reserva por carrera (BigQuery sin locks) — resuelto con `try_lock_cita_slot`.
- ✅ Llamadas bloqueantes en handlers async — resuelto con `run_in_threadpool`.
- ✅ CI no corría tests antes de deploy — resuelto (job `test` en ambos workflows).
- ➖ Twilio sin validar firma — aceptado como está: solo se usa para demos sin número propio
  configurado, sin fallback URL, riesgo bajo mientras siga así.
- ⏳ `docs/key.json` con credencial real en OneDrive — decisión del usuario de no rotarla por
  ahora; no bloqueante para este trabajo.
- ⏳ `internal_auth.py` compara con `!=` en vez de `hmac.compare_digest` — pendiente, menor.
- ⏳ Dependencias sin lockfile (`pyproject.toml` con `>=` sin pins) — pendiente, menor.
