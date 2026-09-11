# agent/
Núcleo del agente (LangGraph): estado tipado, nodos `retrieve_context`, `agent`, `tools`, `compact`, `finalize`,
decisión `should_continue`, checkpointer PostgresSaver. Ver sección "Flujo del agente" en `docs/plan-asistente-ia-local.md`.

- `graph.py` — el `StateGraph` propio (sin prebuilt) y sus nodos
- `state.py` — estado compartido; los acumuladores se resetean por run
- `runner.py` — ciclo de vida del run: eventos, cancelación, timeout, persistencia
- `events.py` — eventos del protocolo AG-UI y su serialización SSE
- `prompts.py` — system prompt (incluye la regla de datos vs. instrucciones)
- `llm.py` — ChatOllama con `num_ctx`/`num_predict` explícitos y chequeo de salud

El nodo `compact` llega en la Fase 2, junto con el RAG.
