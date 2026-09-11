# db/
Conexión y migraciones de PostgreSQL (imagen `pgvector/pgvector`). Incluye las tablas del checkpointer de LangGraph.

- `repository.py` — acceso a CONVERSATIONS y MESSAGES. Dos implementaciones con la
  misma interfaz: `MemoryRepository` (dev y tests) y `PostgresRepository`
- `migrations/001_mvp.sql` — tablas del MVP. Se aplican solas al arrancar con
  `DATABASE_URL`. Las del checkpointer las crea `PostgresSaver.setup()`

`DOCUMENTS` y `DOCUMENT_CHUNKS` (con índice HNSW) llegan en la Fase 2.
