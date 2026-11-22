"""Adaptador OpenRouter: Anthropic exclusivamente, BYOK y sin reintentos implícitos."""

import json
import ssl

import httpx
from pydantic import ValidationError

from app.generation import GenerationResponse
from app.models import AIModel
from app.prompts import validate_generation_envelope, validate_generation_output

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def tls_context():
    """Almacén estándar Python/OpenSSL, incluido SSL_CERT_FILE si está configurado."""
    return ssl.create_default_context()


class OpenRouterError(RuntimeError):
    """Solo mensajes controlados; nunca cuerpos HTTP ni cabeceras de autenticación."""


class OpenRouterProvider:
    def __init__(self, settings, transport=None):
        if settings.openrouter_api_key is None:
            raise OpenRouterError("Configura OPENROUTER_API_KEY en el .env local")
        key = settings.openrouter_api_key.get_secret_value()
        if len(key) < 20 or any(part in key.lower() for part in ("change_me", "replace_me")):
            raise OpenRouterError("OPENROUTER_API_KEY no contiene una clave válida")
        self.settings = settings
        self.transport = transport
        self.model = AIModel(proveedor="openrouter/anthropic", modelo=settings.openrouter_model)

    async def generate(self, request):
        body = {
            "model": self.model.modelo,
            "messages": [{"role": "user", "content": request.prompt}],
            "stream": False,
            "max_completion_tokens": self.settings.openrouter_max_completion_tokens,
            "provider": {
                "only": ["anthropic"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "banco_preguntas",
                    "strict": True,
                    "schema": request.output_schema,
                },
            },
        }
        try:
            # Endpoint fijo, TLS verificado; no seguir redirects ni reintentar la llamada.
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=self.settings.bank_refill_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                verify=tls_context(),
            ) as client:
                response = await client.post(
                    ENDPOINT,
                    json=body,
                    headers={
                        "Authorization": "Bearer "
                        + self.settings.openrouter_api_key.get_secret_value(),
                        "X-OpenRouter-Title": "TrAIner",
                        "X-OpenRouter-Metadata": "enabled",
                    },
                )
        except httpx.TimeoutException:
            raise OpenRouterError(
                "OpenRouter agotó el tiempo de espera; no se ha reintentado"
            ) from None
        except httpx.RequestError:
            raise OpenRouterError(
                "No se pudo conectar con OpenRouter; revisa red y certificados"
            ) from None
        if response.status_code != 200:
            raise OpenRouterError(
                f"OpenRouter devolvió HTTP {response.status_code}; no se ha reintentado"
            )
        try:
            if len(response.content) > 1024 * 1024:
                raise ValueError("Respuesta demasiado grande")
            data = response.json()
            if not isinstance(data, dict) or "error" in data:
                raise ValueError("Respuesta de error")
            usage = data.get("usage") or {}
            routing = data.get("openrouter_metadata") or {}
            flags = [v for v in (usage.get("is_byok"), routing.get("is_byok")) if v is not None]
            if not flags or any(flag is not True for flag in flags):
                raise OpenRouterError(
                    "OpenRouter no confirmó BYOK; revisa Shared capacity fallback del workspace"
                )
            provider = data.get("provider")
            if provider is None:
                selected = [
                    p["provider"]
                    for p in routing.get("endpoints", {}).get("available", [])
                    if p.get("selected") is True
                ]
                provider = selected[0] if len(selected) == 1 else None
            if not isinstance(provider, str) or provider.casefold() != "anthropic":
                raise OpenRouterError("OpenRouter no confirmó Anthropic como proveedor")
            if data.get("model") != self.model.modelo:
                raise OpenRouterError(
                    "El modelo devuelto por OpenRouter no coincide con el solicitado"
                )
            choice = data["choices"][0]
            if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
                raise OpenRouterError("OpenRouter no devolvió una generación completa y aceptada")
            payload = json.loads(choice["message"]["content"])
            validated_payload = (
                validate_generation_envelope(payload)
                if request.validacion_parcial
                else validate_generation_output(payload, request.parameters).model_dump()
            )
            return GenerationResponse(
                payload=validated_payload,
                tokens_entrada=usage.get("prompt_tokens"),
                tokens_salida=usage.get("completion_tokens"),
                id_llamada=data.get("id"),
                modelo_utilizado=data["model"],
                proveedor_utilizado=provider,
                byok_confirmado=True,
                # El coste de OpenRouter no representa necesariamente la factura BYOK de Anthropic.
                coste_estimado_usd=None,
            )
        except OpenRouterError:
            raise
        except json.JSONDecodeError:
            raise OpenRouterError("OpenRouter devolvió un JSON global ilegible") from None
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, ValidationError):
            raise OpenRouterError(
                "La respuesta de OpenRouter incumple el contrato de generación"
            ) from None
