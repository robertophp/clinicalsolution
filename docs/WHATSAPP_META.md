# WhatsApp Cloud API (Meta) con este backend

Este proyecto puede recibir mensajes de **WhatsApp Business** vía **Meta** en `POST /webhooks/whatsapp` y responder con la **Graph API**. El canal **Twilio** sigue disponible en `POST /whatsapp?clinic_id=...` (útil mientras `demo_clinic_2` u otras líneas no migran).

## Qué necesitas en Meta (resumen)

1. **App** en [Meta for Developers](https://developers.facebook.com) con producto **WhatsApp**.
2. **WABA ID** (WhatsApp Business Account): referencia del negocio (no es el mismo que el Phone Number ID).
3. **Phone number ID**: aparece en **WhatsApp → API Setup**; es el ID que debes copiar a `whatsapp_phone_number_id` en [`src/backend/data/clinics/<clinic_id>/site.json`](../src/backend/data/clinics/demo_clinic_1/site.json) (o la carpeta de tu clínica). Sin este campo, el webhook no sabe qué clínica es.
4. **Access token** de la app (temporal para pruebas o de larga duración en producción): va en `.env` como `META_WHATSAPP_ACCESS_TOKEN`.
5. **App Secret**: va en `.env` como `META_APP_SECRET` (no lo subas a git).
6. **Verify token**: string que inventas tú; el mismo valor en `.env` (`META_WEBHOOK_VERIFY_TOKEN`) y en la configuración del webhook en Meta.

## Variables en `.env`

Ver [`.env.example`](../.env.example). Mínimo para Meta:

- `META_APP_SECRET`
- `META_WHATSAPP_ACCESS_TOKEN`
- `META_WEBHOOK_VERIFY_TOKEN`
- Opcional: `META_WABA_ID` (solo referencia / logs)
- Opcional: `META_WEBHOOK_SKIP_SIGNATURE_VERIFY=true` **solo en local** si depuras sin firma (no en producción).

## ngrok (fase actual)

1. Arranca el backend: `uvicorn backend.main:app --reload --port 8000`
2. En otra terminal: `ngrok http 8000`
3. Copia la URL **HTTPS** (ej. `https://xxxx.ngrok-free.app`).
4. En Meta → tu App → WhatsApp → **Configuration** → Webhook:
   - **Callback URL**: `https://TU-NGROK/webhooks/whatsapp`
   - **Verify token**: el mismo que `META_WEBHOOK_VERIFY_TOKEN`
   - Suscripción al campo **`messages`**

Meta hará un **GET** a esa URL para verificar; el backend responde con el `hub.challenge`.

## Configurar la clínica 1

En `data/clinics/demo_clinic_1/site.json`, pon:

```json
"whatsapp_phone_number_id": "TU_PHONE_NUMBER_ID_DESDE_META"
```

El número visible (ej. 503 7021 1900) **no** es el Phone Number ID; el ID es numérico largo que muestra la consola de Meta junto al número de prueba/producción.

## Seguridad

- Rota el **App Secret** si se expuso en un chat o repositorio.
- No commitees `.env`.

## Endpoints

| Método | Ruta | Uso |
|--------|------|-----|
| GET | `/webhooks/whatsapp` | Verificación Meta (`hub.mode`, `hub.verify_token`, `hub.challenge`) |
| POST | `/webhooks/whatsapp` | Eventos entrantes (JSON); respuesta vía Graph API |
| POST | `/whatsapp?clinic_id=...` | Twilio (form `From`, `Body`) |

Diagnóstico rápido: `GET /health/meta` (comprueba qué variables Meta están cargadas y qué `phone_number_id` hay mapeados).
