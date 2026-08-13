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
- [ ] Commit + push de `feature/infra` → `dev`; validar que `clinicalsolution-stg` sigue
      desplegando bien con el job de tests corriendo y el Environment `dev` enlazado.
- [ ] Promover `dev` → `main` para que `deploy-prod.yml` cree `clinicalsolution-prod` por
      primera vez (el servicio no existe aún en GCP — lo crea `gcloud run deploy` solo, no
      hace falta crearlo a mano en la consola).
- [ ] Verificar arranque limpio del nuevo servicio (`GET /health/gcp` con `INTERNAL_API_KEY`).
- [ ] Revisar `clinica_datos.citas` por citas activas con fecha futura antes del corte
      (no se migran — decidir caso por caso si aparecen).
- [ ] Corte: reapuntar la URL de webhook en Meta Developer Console hacia
      `clinicalsolution-prod`.
- [ ] Verificar con un mensaje real que la cita cae en `cita_datos_prod`, no en
      `clinica_datos`.
- [ ] `clinicalsolution-stg` queda viva como red de seguridad — no se apaga ni se borra tras
      el corte.

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
