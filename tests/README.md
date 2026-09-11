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
