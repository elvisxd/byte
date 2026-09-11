"""Embeddings con Ollama (nomic-embed-text por defecto).

Se habla con la API HTTP directamente, igual que agent/llm.py con /api/tags: es
una sola llamada y evita sumar langchain-community como dependencia.
"""

import httpx

from api.config import Settings
from api.logging import get_logger

logger = get_logger("rag.embeddings")

# Dimensión de nomic-embed-text, fijada en la columna vector(768) de la
# migración 002. Cambiar de modelo obliga a migrar la columna y reindexar.
EMBEDDING_DIM = 768


class EmbeddingError(RuntimeError):
    """Ollama no respondió o devolvió algo que no sirve."""


class Embedder:
    def __init__(self, base_url: str, model: str, timeout_s: float = 60.0) -> None:
        self._url = f"{base_url.rstrip('/')}/api/embed"
        self._model = model
        self._timeout_s = timeout_s

    async def embed(self, textos: list[str]) -> list[list[float]]:
        """Vectoriza en una sola llamada.

        /api/embed acepta una lista y devuelve los vectores en orden, así que
        indexar un documento no cuesta una request por chunk.
        """
        if not textos:
            return []

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                respuesta = await client.post(
                    self._url, json={"model": self._model, "input": textos}
                )
                respuesta.raise_for_status()
                vectores = respuesta.json().get("embeddings") or []
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("embeddings_fallaron", error_type=type(exc).__name__)
            raise EmbeddingError(str(exc)) from exc

        # Un vector de dimensión equivocada no entra en la columna y rompería el
        # INSERT a mitad de camino: mejor fallar acá, con el motivo claro.
        if len(vectores) != len(textos):
            raise EmbeddingError(f"se pidieron {len(textos)} vectores y llegaron {len(vectores)}")
        for vector in vectores:
            if len(vector) != EMBEDDING_DIM:
                raise EmbeddingError(
                    f"el modelo {self._model} devuelve vectores de {len(vector)}, "
                    f"y la tabla espera {EMBEDDING_DIM}"
                )
        return vectores

    async def embed_one(self, texto: str) -> list[float]:
        """Un solo texto: lo que necesita la consulta de búsqueda."""
        vectores = await self.embed([texto])
        return vectores[0]


def build_embedder(settings: Settings) -> Embedder:
    return Embedder(
        base_url=settings.ollama_base_url,
        model=settings.ollama_embed_model,
        timeout_s=settings.embed_timeout_s,
    )
