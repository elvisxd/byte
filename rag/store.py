"""Almacenamiento de documentos y chunks sobre Postgres + pgvector.

Va aparte del Repository de db/ a propósito: el RAG necesita pgvector, y una
implementación en memoria sería una simulación que no se parece a la real
(sin índice HNSW, sin tsvector, con otro ranking). Sin Postgres, Byte arranca
sin las herramientas de RAG, igual que arranca sin web_search cuando falta la
TAVILY_API_KEY.

Toda consulta filtra por user_id (docs/seguridad-byte.md): los documentos de un
usuario no pueden aparecer en la búsqueda de otro.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from api.logging import get_logger

logger = get_logger("rag.store")

# Peso de cada mitad en la búsqueda híbrida. El vector manda porque captura
# sinónimos y parafraseo; el léxico rescata nombres propios, identificadores y
# códigos de error, que el embedding difumina.
#
# Se aplican sobre posiciones (RRF), no sobre los scores crudos: ver `search`.
PESO_VECTOR = 0.7
PESO_LEXICO = 0.3
# La constante de RRF: amortigua cuánto gana el primer puesto sobre el segundo.
# 60 es el valor del paper original y el que usan Elasticsearch y pgvector en
# sus ejemplos; más chico vuelve el primer puesto casi imbatible.
RRF_K = 60

# FASE 4 (multi-usuario con JWT): hoy no hay usuarios, así que todo se guarda y
# se busca con user_id NULL. Las consultas ya filtran por la columna, pero
# mientras el valor sea siempre el mismo el filtro nunca discrimina. Cuando
# llegue el JWT hay que reemplazar los usos de esta constante por el usuario del
# request — buscarla da los puntos exactos, que es para lo que existe.
# Nota: db/repository.py (conversaciones y mensajes) todavía no tiene el
# parámetro, así que esa mitad es una refactorización aparte.
SIN_USUARIO: str | None = None


@dataclass(frozen=True, slots=True)
class Document:
    id: str
    filename: str
    status: str
    size_bytes: int
    mime_type: str
    chunks: int
    uploaded_at: Any
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class ChunkHit:
    chunk_id: str
    document_id: str
    filename: str
    content: str
    score: float


def _vector_literal(vector: list[float]) -> str:
    """pgvector acepta el vector como texto '[1,2,3]'.

    Se formatea a mano para no sumar el paquete `pgvector` solo por esto.
    """
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _es_uuid(value: str) -> bool:
    """Un id inválido en una columna uuid explota como error 500.

    Mismo guard que db/repository.py: los ids llegan de la URL, así que
    cualquiera puede mandar cualquier cosa.
    """
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


class DocumentStore:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def create(
        self, *, filename: str, size_bytes: int, mime_type: str, user_id: str | None
    ) -> str:
        """Registra el documento en 'processing' y devuelve su id."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO documents (user_id, filename, size_bytes, mime_type)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, filename, size_bytes, mime_type),
            )
            fila = await cur.fetchone()
        return str(fila["id"])

    async def save_chunks(
        self, document_id: str, chunks: list[tuple[int, str, list[float]]]
    ) -> None:
        """Guarda los chunks y marca el documento como indexado.

        Todo en una transacción: si algo falla a mitad, el documento no queda
        'indexed' con la mitad de sus chunks, que daría búsquedas incompletas
        sin ningún aviso.
        """
        async with self._pool.connection() as conn, conn.transaction():
            # Un reindex reemplaza: sin esto los chunks viejos se duplicarían.
            await conn.execute("DELETE FROM document_chunks WHERE document_id = %s", (document_id,))
            for indice, contenido, vector in chunks:
                await conn.execute(
                    """
                    INSERT INTO document_chunks (document_id, chunk_index, content, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    """,
                    (document_id, indice, contenido, _vector_literal(vector)),
                )
            await conn.execute(
                "UPDATE documents SET status = 'indexed', chunks = %s, error_message = NULL "
                "WHERE id = %s",
                (len(chunks), document_id),
            )

    async def recuperar_huerfanos(self) -> int:
        """Cierra los documentos que quedaron indexándose cuando se cayó el proceso.

        Las tareas de ingesta viven en memoria: si Byte se reinicia a mitad, el
        documento queda en 'processing' para siempre. La búsqueda los ignora
        (filtra por 'indexed'), así que sin esto son invisibles y eternos.
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE documents SET status = 'error', "
                "error_message = 'la indexación se interrumpió; volvé a subir el archivo' "
                "WHERE status = 'processing'"
            )
        return cur.rowcount

    async def mark_error(self, document_id: str, mensaje: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE documents SET status = 'error', error_message = %s WHERE id = %s",
                # El mensaje se acota: puede venir de una excepción con un
                # traceback entero adentro.
                (mensaje[:500], document_id),
            )

    async def get(self, document_id: str, user_id: str | None) -> Document | None:
        if not _es_uuid(document_id):
            return None
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT id, filename, status, size_bytes, mime_type, chunks,
                       uploaded_at, error_message
                FROM documents
                WHERE id = %s AND user_id IS NOT DISTINCT FROM %s
                """,
                (document_id, user_id),
            )
            fila = await cur.fetchone()
        return _to_document(fila) if fila else None

    # No se llama `list`: dentro del cuerpo de la clase pisaría al builtin y
    # rompería las anotaciones `list[float]` de los métodos de abajo.
    async def list_documents(self, user_id: str | None, limit: int = 50) -> list[Document]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT id, filename, status, size_bytes, mime_type, chunks,
                       uploaded_at, error_message
                FROM documents
                WHERE user_id IS NOT DISTINCT FROM %s
                ORDER BY uploaded_at DESC, id DESC
                LIMIT %s
                """,
                (user_id, limit),
            )
            filas = await cur.fetchall()
        return [_to_document(fila) for fila in filas]

    async def delete(self, document_id: str, user_id: str | None) -> bool:
        """Borra el documento; los chunks se van por el ON DELETE CASCADE."""
        if not _es_uuid(document_id):
            return False
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM documents WHERE id = %s AND user_id IS NOT DISTINCT FROM %s",
                (document_id, user_id),
            )
        return cur.rowcount > 0

    async def search(
        self, *, query: str, embedding: list[float], user_id: str | None, top_k: int
    ) -> list[ChunkHit]:
        """Búsqueda híbrida por fusión de rankings (RRF).

        Se combinan las **posiciones** de cada mitad, no sus scores. Sumarlos
        directamente no funciona: el coseno está normalizado en [0,1] y en la
        práctica cae entre 0.4 y 0.75, mientras que `ts_rank` no está acotado y
        devuelve del orden de 0.004 a 0.1. Medido sobre los documentos de
        prueba, el término léxico aportaba ~0.03 contra ~0.5 del vector: pesaba
        un orden de magnitud menos que su peso nominal, y no rescataba los
        nombres propios e identificadores que es justo para lo que está.

        RRF (1/(k+posición)) es indiferente a la escala: solo le importa qué
        salió primero en cada lista. `websearch_to_tsquery` tolera texto libre
        del usuario sin romper con la sintaxis de tsquery.

        Con el corpus de prueba (2 documentos, ~8 chunks) las dos fórmulas
        devuelven lo mismo en 8 de 8 consultas: el vector ya domina cuando hay
        tan poco donde elegir. La diferencia se paga con muchos documentos, que
        es cuando el ranking léxico tiene contra quién competir. Si algún día
        hay corpus real, vale la pena volver a medirlo antes de tocar los pesos.
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                WITH candidatos AS (
                    SELECT c.id, c.document_id, c.content, d.filename,
                           RANK() OVER (ORDER BY c.embedding <=> %s::vector) AS pos_vector,
                           RANK() OVER (
                               ORDER BY COALESCE(
                                   ts_rank(c.content_search,
                                           websearch_to_tsquery('simple', %s)), 0
                               ) DESC
                           ) AS pos_lexico,
                           COALESCE(
                               ts_rank(c.content_search,
                                       websearch_to_tsquery('simple', %s)), 0
                           ) AS lexico
                    FROM document_chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE d.user_id IS NOT DISTINCT FROM %s
                      AND d.status = 'indexed'
                      AND c.embedding IS NOT NULL
                )
                SELECT id, document_id, filename, content,
                       %s / (%s + pos_vector)
                       -- El lado léxico solo suma si hubo coincidencia real: sin
                       -- ella todos empatan en la misma posición y el ranking
                       -- sería ruido con el mismo peso que una coincidencia.
                       + CASE WHEN lexico > 0 THEN %s / (%s + pos_lexico) ELSE 0 END
                       AS score
                FROM candidatos
                ORDER BY score DESC
                LIMIT %s
                """,
                (
                    _vector_literal(embedding),
                    query,
                    query,
                    user_id,
                    PESO_VECTOR,
                    RRF_K,
                    PESO_LEXICO,
                    RRF_K,
                    top_k,
                ),
            )
            filas = await cur.fetchall()
        return [
            ChunkHit(
                chunk_id=str(fila["id"]),
                document_id=str(fila["document_id"]),
                filename=fila["filename"],
                content=fila["content"],
                score=float(fila["score"]),
            )
            for fila in filas
        ]


def _to_document(fila: dict[str, Any]) -> Document:
    return Document(
        id=str(fila["id"]),
        filename=fila["filename"],
        status=fila["status"],
        size_bytes=fila["size_bytes"],
        mime_type=fila["mime_type"],
        chunks=fila["chunks"],
        uploaded_at=fila["uploaded_at"],
        error_message=fila["error_message"],
    )
