-- Byte — esquema del MVP (Fase 0).
-- ERD completo en docs/plan-asistente-ia-local.md. Acá van solo las tablas que
-- el MVP usa: USERS, CONVERSATIONS y MESSAGES.
-- DOCUMENTS y DOCUMENT_CHUNKS (con pgvector + HNSW) llegan en la Fase 2.
-- Las tablas del checkpointer de LangGraph las crea PostgresSaver.setup().

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text UNIQUE NOT NULL,
    password_hash text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversations (
    -- user_id queda nullable hasta la Fase 4 (multi-usuario con JWT): en el MVP
    -- hay una sola credencial, pero la columna ya existe para no migrar después.
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  uuid REFERENCES users (id) ON DELETE CASCADE,
    title                    text NOT NULL,
    summary                  text,
    summary_up_to_message_id uuid,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now()
);

-- El sidebar ordena por updated_at desc y pagina por cursor.
CREATE INDEX IF NOT EXISTS conversations_updated_at_idx
    ON conversations (updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS messages (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id   uuid NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role              text NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content           text NOT NULL,
    metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
    langfuse_trace_id text,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_conversation_idx
    ON messages (conversation_id, created_at DESC, id DESC);
