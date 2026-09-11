# db/
Conexión y migraciones de PostgreSQL (imagen `pgvector/pgvector`). Incluye las tablas del checkpointer de LangGraph.

- `repository.py` — acceso a CONVERSATIONS y MESSAGES. Dos implementaciones con la
  misma interfaz: `MemoryRepository` (dev y tests) y `PostgresRepository`
- `migrations/001_mvp.sql` — tablas del MVP (USERS, CONVERSATIONS, MESSAGES)
- `migrations/002_rag.sql` — extensión `vector`, DOCUMENTS y DOCUMENT_CHUNKS con
  índice HNSW y `tsvector` para la búsqueda híbrida

Se aplican solas al arrancar con `DATABASE_URL`. Las del checkpointer las crea
`PostgresSaver.setup()`.

Las migraciones se registran en `schema_migrations` y se aplican **una sola
vez**, cada una en su transacción y con un advisory lock para que dos instancias
no compitan. Para agregar una, poné un archivo nuevo con número mayor: no
edites uno ya aplicado.

El acceso a documentos y chunks **no** va por `repository.py` sino por
`rag/store.py`: el RAG necesita pgvector y una implementación en memoria sería
una simulación con otro ranking.
