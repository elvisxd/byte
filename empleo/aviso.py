"""Mandar el aviso al teléfono.

Va por el mismo camino que el vigía de `paper/`: un POST al panel, que es quien
tiene el token del bot de Telegram. La Mac no lo tiene a propósito —un secreto
menos en la máquina de uso diario— y ese camino ya está probado en producción,
así que no se inventa uno nuevo para esto.

Best-effort: si el panel no contesta, el cazador sigue. El digest queda escrito
en disco igual, y perder un aviso no puede costar las ofertas del día.
"""

import json
import os
import urllib.error
import urllib.request

from api.logging import get_logger

logger = get_logger("empleo.aviso")

TIMEOUT_S = 10
# Telegram corta los mensajes en 4096 caracteres. El panel reenvía tal cual, así
# que lo que sobre no llega: mejor recortar acá, avisando, que mandar un mensaje
# que termina a mitad de un link.
MAX_CARACTERES = 3800


def avisar(texto: str) -> bool:
    """Manda el texto al panel, que lo reenvía a Telegram. Nunca lanza."""
    destino = os.environ.get("PANEL_URL", "")
    if not destino:
        logger.info("aviso_sin_panel", detail="PANEL_URL vacío: el digest queda solo en disco")
        return False

    if len(texto) > MAX_CARACTERES:
        texto = texto[:MAX_CARACTERES] + "\n[...recortado; el resto, en el digest del disco]"

    pedido = urllib.request.Request(  # noqa: S310 - destino fijado por entorno, no por el modelo
        destino.rstrip("/") + "/api/papel/aviso",
        data=json.dumps({"texto": texto}, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('PANEL_TOKEN', '')}",
        },
    )
    try:
        with urllib.request.urlopen(pedido, timeout=TIMEOUT_S) as respuesta:  # noqa: S310
            respuesta.read()
    except (OSError, urllib.error.URLError, ValueError) as exc:
        logger.warning("aviso_fallo", error_type=type(exc).__name__)
        return False
    return True
