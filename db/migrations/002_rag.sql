-- Byte — RAG (Fase 2): DOCUMENTS y DOCUMENT_CHUNKS.
-- ERD completo en docs/plan-asistente-ia-local.md.
--
-- La extensión vector la trae la imagen pgvector/pgvector:pg16 (docker/ y CI).
-- Si la migración falla acá, el Postgres no es esa imagen: sin vector no hay RAG.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    -- user_id nullable como en conversations: la Fase 4 trae el multi-usuario,
    -- pero las consultas ya filtran por esta columna (docs/seguridad-byte.md).
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid REFERENCES users (id) ON DELETE CASCADE,
    filename    text NOT NULL,
    status      text NOT NULL DEFAULT 'processing'
                CHECK (status IN ('processing', 'indexed', 'error')),
    error_message text,
    size_bytes  bigint NOT NULL,
    mime_type   text NOT NULL,
    chunks      integer NOT NULL DEFAULT 0,
    uploaded_at timestamptz NOT NULL DEFAULT now()
);

-- La pantalla de carga lista los documentos del usuario, más nuevos primero.
CREATE INDEX IF NOT EXISTS documents_user_uploaded_idx
    ON documents (user_id, uploaded_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS document_chunks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    content     text NOT NULL,
    -- 768 = nomic-embed-text. Cambiar de modelo de embeddings obliga a migrar
    -- esta columna y a reindexar: los vectores de modelos distintos no se
    -- pueden comparar entre sí.
    embedding   vector(768),
    -- Mitad léxica de la búsqueda híbrida. Generada: no puede quedar desfasada
    -- del content. 'simple' y no 'spanish' a propósito: el contenido puede estar
    -- en cualquier idioma y un stemmer equivocado es peor que ninguno.
    content_search tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    UNIQUE (document_id, chunk_index)
);

-- Borrar un documento borra sus chunks por el ON DELETE CASCADE de arriba;
-- este índice hace que ese borrado (y el reindex) no escaneen la tabla entera.
CREATE INDEX IF NOT EXISTS document_chunks_document_idx
    ON document_chunks (document_id);

-- HNSW sobre coseno: es la distancia que usa nomic-embed-text.
-- Se crea con la tabla vacía a propósito. pgvector lo va llenando a medida que
-- entran los chunks; construirlo después sobre muchas filas bloquea la tabla.
CREATE INDEX IF NOT EXISTS document_chunks_embedding_idx
    ON document_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS document_chunks_search_idx
    ON document_chunks USING gin (content_search);
