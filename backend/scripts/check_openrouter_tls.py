"""Comprueba TLS contra el catálogo público, sin clave y sin generar contenido."""

import asyncio
import json
import ssl
import urllib.request

import httpx

from app.openrouter import tls_context

MODELS_URL = "https://openrouter.ai/api/v1/models"


async def check():
    context = tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    # No usar proxies/autenticación del entorno ni instanciar el proveedor.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
    )
    with opener.open(MODELS_URL, timeout=20) as response:
        urllib_status = response.status
    async with httpx.AsyncClient(verify=context, trust_env=False, timeout=20) as client:
        response = await client.get(MODELS_URL)
        response.raise_for_status()
        assert "authorization" not in response.request.headers
        return {
            "urllib_http": urllib_status,
            "httpx_http": response.status_code,
            "modelos": len(response.json()["data"]),
            "verificacion_tls": True,
            "verificacion_hostname": context.check_hostname,
            "autenticacion_enviada": False,
        }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(check()), ensure_ascii=False, indent=2))
