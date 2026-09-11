"""Ingesta de documentos: parsear, chunkear, vectorizar e indexar.

El upload responde 202 y esto corre después en segundo plano (contrato de la
API). Por eso nada de acá levanta excepciones hacia afuera: cualquier problema
termina en el documento marcado 'error' con su motivo, que es lo que la pantalla
de carga muestra.
"""

import asyncio
from dataclasses import dataclass

from api.logging import get_logger
from rag.chunking import split
from rag.embeddings import Embedder, EmbeddingError
from rag.parsing import ParseError, parse
from rag.store import DocumentStore

logger = get_logger("rag.ingest")


@dataclass(slots=True)
class Ingestor:
    store: DocumentStore
    embedder: Embedder
    parse_timeout_s: float
    batch_size: int

    async def index(self, document_id: str, data: bytes, filename: str) -> None:
        """Indexa un documento ya registrado en la base.

        Idempotente: save_chunks borra los chunks previos, así que reintentar
        sobre el mismo documento reemplaza en vez de duplicar.
        """
        try:
            parsed = await parse(data, filename, self.parse_timeout_s)
            chunks = split(parsed.text)
            if not chunks:
                raise ParseError("no quedó texto para indexar")

            vectores: list[list[float]] = []
            # En lotes: un documento grande en una sola llamada puede pasarse del
            # timeout de Ollama, y en CPU los embeddings no son gratis.
            for inicio in range(0, len(chunks), self.batch_size):
                lote = chunks[inicio : inicio + self.batch_size]
                vectores.extend(await self.embedder.embed([c.content for c in lote]))

            await self.store.save_chunks(
                document_id,
                [(c.index, c.content, v) for c, v in zip(chunks, vectores, strict=True)],
            )
            logger.info("documento_indexado", document_id=document_id, chunks=len(chunks))

        except (ParseError, EmbeddingError) as exc:
            # Errores esperables (archivo roto, Ollama caído): el motivo le sirve
            # a quien subió el archivo.
            await self._marcar_error(document_id, str(exc))
        except asyncio.CancelledError:
            # El proceso se está apagando: queda 'processing' y se reindexa
            # después. No se marca error, porque el documento no tiene nada malo.
            raise
        except Exception as exc:  # noqa: BLE001 - nada puede tumbar la tarea de fondo
            logger.exception("indexado_fallo", document_id=document_id)
            await self._marcar_error(document_id, f"error inesperado: {type(exc).__name__}")

    async def _marcar_error(self, document_id: str, motivo: str) -> None:
        try:
            await self.store.mark_error(document_id, motivo)
        except Exception:  # noqa: BLE001 - si ni esto anda, solo queda el log
            logger.exception("no_se_pudo_marcar_error", document_id=document_id)
