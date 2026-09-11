# tests/
Tests con pytest. No necesitan Ollama, Tavily ni Postgres: `fakes.py` tiene los
dobles (un LLM que se puede guionar, uno lento, uno roto y un cliente de búsqueda).

```bash
uv run pytest -q
```

- `test_health_y_seguridad.py` — salud, cabeceras, formato de errores
- `test_auth.py` — API key, cookie de sesión, `events_token`
- `test_conversaciones.py` — CRUD, paginación, título automático
- `test_runs_sse.py` — ciclo del run, SSE, reconexión, cancelación, límites
- `test_agente.py` — el grafo: loop, tope de iteraciones, inyección de prompts
- `test_web_search.py` — la herramienta de búsqueda por separado
- `test_code_exec.py` — la herramienta de código y `POST /execute`
- `test_modo_seguro.py` — el HITL: pausa, aprobación, rechazo y el resume_token
- `test_bordes.py` — casos borde y garantías fáciles de romper sin darse cuenta
- `test_repositorio.py` — el contrato del repositorio contra **las dos**
  implementaciones, más el checkpointer y la API sobre Postgres
- `test_rag.py` — chunking, parseo, embeddings, ingesta y la acumulación del
  resumen (`rag/store.py` necesita pgvector: se prueba a mano, no acá)
- `test_compact_endpoint.py` — `POST /compact`: que saque los mensajes del hilo
  sin llevarse el system prompt ni romper el pareo de tool calls
- `test_pausas_y_memoria.py` — aprobaciones pendientes y recorte del historial
- `test_evals.py` — que el juez de los evals no dé falsos verdes

## Con Postgres

Los tests de `test_repositorio.py` corren contra Postgres si hay uno:

```bash
cd docker && docker compose up -d postgres && cd ..
BYTE_TEST_DATABASE_URL=postgresql://byte:byte@localhost:5432/byte uv run pytest -q
```

Sin la variable se saltean. Correr los mismos tests contra las dos
implementaciones es lo que encontró el bug de paginación del PR #9: en memoria
pasaba, en Postgres reventaba.
