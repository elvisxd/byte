"""Construcción del modelo (Ollama) y chequeo de que esté vivo."""

from typing import Any

import httpx

from api.config import Settings
from api.logging import get_logger

logger = get_logger("agent.llm")


def build_llm(settings: Settings) -> Any:
    """ChatOllama con el contexto y el tope de tokens explícitos.

    Ollama arranca en 4.096 tokens de contexto aunque el modelo soporte más, así
    que `num_ctx` se setea siempre. `num_predict` acota el gasto por respuesta.
    """
    from langchain_ollama import ChatOllama

    return ChatOllama(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        num_ctx=settings.ollama_num_ctx,
        num_predict=settings.ollama_num_predict,
        temperature=0.2,
        reasoning=False,
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
