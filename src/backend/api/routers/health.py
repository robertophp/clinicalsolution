from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from ...bootstrap import WHATSAPP_PHONE_NUMBER_ID_TO_CLINIC, conversation_memory, gemini_service, settings

router = APIRouter(tags=["health"])


@router.get("/health", response_class=Response)
async def healthcheck() -> Response:
    """Sencillo healthcheck para verificar que la app está viva."""
    return Response(content="OK", media_type="text/plain")


@router.get("/health/gcp")
async def healthcheck_gcp() -> dict:
    """
    Diagnóstico de configuración GCP: credenciales, Firestore y Vertex AI (Gemini).
    Útil para ver qué falla antes de probar por WhatsApp.
    """
    result: dict = {
        "config": {"project_id": settings.PROJECT_ID, "location": settings.LOCATION},
        "firestore": None,
        "gemini": None,
    }

    try:
        conversation_memory.get_recent_messages("_health_check", "+0000000000")
        result["firestore"] = "ok"
    except Exception as e:  # noqa: BLE001
        result["firestore"] = f"error: {type(e).__name__}: {e}"

    try:
        reply = gemini_service.generate_reply(
            system_prompt="Eres un asistente. Responde solo: OK.",
            chat_history=[{"role": "user", "content": "Di hola"}],
            max_output_tokens=10,
        )
        result["gemini"] = "ok" if reply else "empty_response"
    except Exception as e:  # noqa: BLE001
        result["gemini"] = f"error: {type(e).__name__}: {e}"

    return result


@router.get("/health/meta")
async def health_meta() -> dict:
    """
    Comprueba que las variables Meta estén cargadas (sin exponer secretos)
    y qué phone_number_id están mapeados a clínicas.
    """
    return {
        "meta_waba_id_configured": bool((settings.META_WABA_ID or "").strip()),
        "meta_access_token_configured": bool((settings.META_WHATSAPP_ACCESS_TOKEN or "").strip()),
        "meta_verify_token_configured": bool((settings.META_WEBHOOK_VERIFY_TOKEN or "").strip()),
        "meta_app_secret_configured": bool((settings.META_APP_SECRET or "").strip()),
        "meta_webhook_skip_signature": settings.META_WEBHOOK_SKIP_SIGNATURE_VERIFY,
        "graph_api_version": settings.META_GRAPH_API_VERSION,
        "whatsapp_phone_number_ids_mapped": WHATSAPP_PHONE_NUMBER_ID_TO_CLINIC,
    }
