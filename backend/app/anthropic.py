"""Anthropic Messages directo; una petición, TLS verificado y sin publicación."""

import json

import httpx
from pydantic import ValidationError

from app.generation import GenerationResponse
from app.models import AIModel
from app.openrouter import tls_context
from app.prompts import validate_generation_output

ENDPOINT = "https://api.anthropic.com/v1/messages"


class AnthropicError(RuntimeError):
    """Mensajes controlados, sin cuerpos HTTP ni credenciales."""

    def __init__(self, message, diagnostic=None):
        super().__init__(message)
        self.diagnostic = diagnostic


def validation_details(error, redact, prefix=""):
    """Conservar rutas/reglas, excluyendo inputs, ctx y URLs de Pydantic."""
    return [
        {
            "campo": prefix + (".".join(redact(str(part)) for part in item["loc"]) or "$"),
            "regla": item["type"],
            "detalle": redact(item["msg"]),
        }
        for item in error.errors(include_input=False, include_context=False, include_url=False)
    ]


def anthropic_schema(schema):
    """Adaptar restricciones no soportadas; el contrato original se valida después.

    https://platform.claude.com/docs/en/build-with-claude/structured-outputs
    No modifica el esquema compartido ni las instrucciones del prompt.
    """
    if isinstance(schema, list):
        return [anthropic_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    result = {}
    constraints = {}
    for key, value in schema.items():
        if key in {"minLength", "maxLength", "maxItems"} or (
            key == "minItems" and value not in (0, 1)
        ):
            constraints[key] = value
        else:
            result[key] = anthropic_schema(value)
    if constraints:
        result["description"] = (
            result.get("description", "") + " Restricciones: " + json.dumps(constraints)
        ).strip()
    return result


class AnthropicProvider:
    def __init__(self, settings, transport=None, capture_text=None):
        if settings.anthropic_api_key is None:
            raise AnthropicError("Configura ANTHROPIC_API_KEY en el .env local")
        key = settings.anthropic_api_key.get_secret_value()
        if len(key) < 20 or any(part in key.lower() for part in ("change_me", "replace_me")):
            raise AnthropicError("ANTHROPIC_API_KEY no contiene una clave válida")
        self.settings = settings
        self.transport = transport
        self.model = AIModel(proveedor="anthropic", modelo=settings.anthropic_model)
        self.capture_text = capture_text

    async def generate(self, request):
        secret = self.settings.anthropic_api_key.get_secret_value()

        def redact(text):
            return text.replace(secret, "[REDACTADO]")

        diagnostic = {
            "ronda": request.ronda,
            "modelo_solicitado": self.model.modelo,
            "modelo_utilizado": None,
            "id_llamada": None,
            "stop_reason": None,
            "tokens_entrada": None,
            "tokens_salida": None,
            "preguntas_recibidas": None,
            "preguntas_solicitadas": request.parameters.cantidad,
        }

        def fail(field, rule, detail, **extra):
            diagnostic["errores"] = [{"campo": field, "regla": rule, "detalle": detail, **extra}]
            raise AnthropicError(
                "La respuesta de Anthropic incumple el contrato de generación", diagnostic
            ) from None

        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=self.settings.bank_refill_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                verify=tls_context(),
            ) as client:
                response = await client.post(
                    ENDPOINT,
                    headers={
                        "x-api-key": self.settings.anthropic_api_key.get_secret_value(),
                        "anthropic-version": "2023-06-01",
                    },
                    json={
                        "model": self.model.modelo,
                        "max_tokens": self.settings.anthropic_max_tokens,
                        "messages": [{"role": "user", "content": request.prompt}],
                        "stream": False,
                        "output_config": {
                            "format": {
                                "type": "json_schema",
                                "schema": anthropic_schema(request.output_schema),
                            }
                        },
                    },
                )
        except httpx.TimeoutException:
            raise AnthropicError(
                "Anthropic agotó el tiempo de espera; no se ha reintentado", diagnostic
            ) from None
        except httpx.RequestError:
            raise AnthropicError(
                "No se pudo conectar con Anthropic; revisa red y certificados", diagnostic
            ) from None
        request_id = response.headers.get("request-id")
        diagnostic["id_llamada"] = redact(request_id)[:200] if request_id else None
        if response.status_code != 200:
            raise AnthropicError(
                f"Anthropic devolvió HTTP {response.status_code}; no se ha reintentado", diagnostic
            )
        if len(response.content) > 1024 * 1024:
            fail("respuesta_http", "tamano_maximo", "La respuesta supera 1 MiB")
        try:
            data = response.json()
        except ValueError:
            fail("respuesta_http", "json_valido", "El envoltorio HTTP no contiene JSON válido")
        if not isinstance(data, dict):
            fail("respuesta_http", "objeto_message", "Se esperaba un objeto Messages")

        for source, target in (
            ("model", "modelo_utilizado"),
            ("id", "id_llamada"),
            ("stop_reason", "stop_reason"),
        ):
            value = data.get(source)
            if isinstance(value, str):
                diagnostic[target] = redact(value)[:200]
        usage = data.get("usage") or {}
        if isinstance(usage, dict):
            for source, target in (
                ("input_tokens", "tokens_entrada"),
                ("output_tokens", "tokens_salida"),
            ):
                value = usage.get(source)
                if type(value) is int and value >= 0:
                    diagnostic[target] = value
        blocks = data.get("content")
        texts = (
            [
                block["text"]
                for block in blocks
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ]
            if isinstance(blocks, list)
            else []
        )
        text = "".join(texts) if texts else None
        payload = None
        json_error = None
        if text is not None:
            try:
                payload = json.loads(text)
                questions = payload.get("preguntas") if isinstance(payload, dict) else None
                if isinstance(questions, list):
                    diagnostic["preguntas_recibidas"] = len(questions)
            except json.JSONDecodeError as error:
                json_error = {"linea": error.lineno, "columna": error.colno}
            if self.capture_text is not None:
                diagnostic.update(self.capture_text(redact(text), diagnostic))
        if data.get("type") != "message" or data.get("role") != "assistant" or "error" in data:
            fail("respuesta_http", "objeto_message", "Se esperaba type=message y role=assistant")
        if data.get("model") != self.model.modelo:
            fail("model", "modelo_solicitado", "El modelo devuelto no coincide con el solicitado")
        if data.get("stop_reason") != "end_turn":
            fail("stop_reason", "end_turn", "Generación incompleta o rechazada")
        if (
            not isinstance(blocks, list)
            or len(blocks) != 1
            or not texts
            or not isinstance(blocks[0], dict)
            or blocks[0].get("type") != "text"
        ):
            fail("content", "un_bloque_texto", "Se requiere exactamente un bloque de texto")
        if json_error is not None:
            fail(
                "content.0.text",
                "json_valido",
                "El texto del modelo no contiene JSON válido",
                **json_error,
            )
        try:
            validated = validate_generation_output(payload, request.parameters)
        except ValidationError as error:
            diagnostic["errores"] = validation_details(error, redact)
            raise AnthropicError(
                "La respuesta de Anthropic incumple el contrato de generación", diagnostic
            ) from None
        except ValueError:
            fail(
                "preguntas",
                "cantidad_exacta",
                "El lote no contiene la cantidad solicitada",
                esperadas=request.parameters.cantidad,
                recibidas=diagnostic["preguntas_recibidas"],
            )
        try:
            return GenerationResponse(
                payload=validated.model_dump(),
                tokens_entrada=usage.get("input_tokens"),
                tokens_salida=usage.get("output_tokens"),
                id_llamada=data.get("id") or response.headers.get("request-id"),
                modelo_utilizado=data["model"],
                proveedor_utilizado="Anthropic",
                # No inventar precios ni tratar una llamada directa como BYOK de OpenRouter.
                coste_estimado_usd=None,
                byok_confirmado=None,
            )
        except ValidationError as error:
            diagnostic["errores"] = validation_details(error, redact, prefix="metadatos.")
            raise AnthropicError(
                "La respuesta de Anthropic incumple el contrato de generación", diagnostic
            ) from None
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            fail("usage", "metadatos_validos", "Los metadatos de la respuesta no son válidos")
