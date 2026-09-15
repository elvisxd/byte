"""Construcción del modelo (Ollama, o Gemini por API) y chequeo de que esté vivo."""

from typing import Any

import httpx

from api.config import Settings
from api.logging import get_logger

logger = get_logger("agent.llm")


def es_de_google(nombre: str) -> bool:
    """Un `gemini-…` se pide a la API de Google; todo lo demás, a Ollama.

    Solo `gemini`: los Gemma también corren en Ollama (`gemma3:4b`) y no se
    quiere que un nombre local se vaya a la red por parecerse.
    """
    return nombre.startswith("gemini")


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
    nombre = modelo or settings.ollama_model
    if es_de_google(nombre):
        return _gemini(settings, nombre, reasoning=reasoning)

    from langchain_ollama import ChatOllama

    return ChatOllama(
        base_url=settings.ollama_base_url,
        model=modelo or settings.ollama_model,
        num_ctx=num_ctx or settings.ollama_num_ctx,
        num_predict=num_predict or settings.ollama_num_predict,
        temperature=0.2,
        reasoning=reasoning,
    )


def _gemini(settings: Settings, nombre: str, *, reasoning: bool) -> Any:
    """El brazo remoto de la comparación. Ver paper/CRITERIO_COMPARACION.md.

    `reasoning` acá no enciende el pensamiento —los Flash 3.x piensan solos— sino
    que pide VERLO (`include_thoughts`): llega como bloques `thinking` en el
    contenido, que el grafo emite igual que el `reasoning_content` de Ollama.

    `max_retries=1`: un solo intento. El reintento ante 429/5xx lo hace el
    relevo cambiando de modelo, que es más útil que insistirle al agotado.

    `num_ctx` y `num_predict` no aplican: el contexto es de un millón y el tope
    de salida tiene el suyo (`gemini_num_predict`, con la medición).
    """
    if not settings.gemini_api_key:
        raise ValueError(f"GEMINI_API_KEY vacía: no se puede armar {nombre}")
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=nombre,
        google_api_key=settings.gemini_api_key,
        temperature=0.2,
        max_output_tokens=settings.gemini_num_predict,
        include_thoughts=True if reasoning else None,
        max_retries=1,
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
