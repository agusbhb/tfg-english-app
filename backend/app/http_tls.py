"""Confianza TLS de Python/OpenSSL compartida por los clientes del backend."""

import ssl


def tls_context():
    """Incluye SSL_CERT_FILE configurado en ejecución; nunca desactiva verificación."""
    return ssl.create_default_context()
