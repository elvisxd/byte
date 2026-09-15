"""Construcción del modelo (Ollama) y chequeo de que esté vivo."""

from typing import Any

import httpx

from api.config import Settings
from api.logging import get_logger

logger = get_logger("agent.llm")


def build_llm(
    settings: Settings,
    modelo: str = "",
    *,
    reasoning: bool = False,
    num_predict: int | None = None,
    num_ctx: int | None = None,
) -> Any:
    """ChatOllama con el contexto y el tope de tokens explícitos.

    Ollama arranca en 4.096 tokens de contexto aunque el modelo soporte más, así
    que `num_ctx` se setea siempre. `num_predict` acota el gasto por respuesta.

    `modelo` permite armar un cliente para un modelo alternativo sin tocar la
    configuración: es lo que usa el cambio en caliente.

    ⚠ `reasoning` VIENE APAGADO Y ASÍ SE QUEDA PARA LA API. Encenderlo cambia el
    coste de cada respuesta, no solo su calidad: medido el 2026-09-14 con
    `qwen3:14b`, la misma pregunta pasó de 88 s a 279 s —de 1,5 a 4,6 minutos— y
    el pensamiento se comió los 1024 tokens de `num_predict` enteros, dejando la
    respuesta VACÍA. Un agente que piensa y no llega a emitir la llamada a la
    herramienta es peor que uno que no piensa.

    Por eso quien lo encienda tiene que subir también `num_predict`: son un par,
    no dos ajustes independientes. El agente en papel lo hace en `paper/sesion.py`;
    la API no lo usa, y su latencia sigue igual que antes.
    """
    from langchain_ollama import ChatOllama

    return ChatOllama(
        base_url=settings.ollama_base_url,
        model=modelo or settings.ollama_model,
        num_ctx=num_ctx or settings.ollama_num_ctx,
        num_predict=num_predict or settings.ollama_num_predict,
        temperature=0.2,
        reasoning=reasoning,
    )


async def ollama_status(base_url: str, model: str, timeout_s: float = 2.0) -> str:
    """ "ok" si responde y tiene el modelo; "sin_modelo" / "caido" si no.

    Alimenta /health/details, que es lo que lee el indicador "En línea" de la UI.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(f"{base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            tags = response.json().get("models", [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ollama_no_responde", error_type=type(exc).__name__)
        return "caido"
    # Ollama devuelve "qwen2.5-coder:7b"; se compara sin el tag para ser tolerante.
    wanted = model.split(":")[0]
    available = {str(tag.get("name", "")).split(":")[0] for tag in tags}
    return "ok" if wanted in available else "sin_modelo"
