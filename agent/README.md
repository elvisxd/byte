# agent/
Núcleo del agente (LangGraph): estado tipado, nodos `retrieve_context`, `agent`, `tools` y `finalize`,
decisión `should_continue`, checkpointer PostgresSaver. Ver sección "Flujo del agente" en `docs/plan-asistente-ia-local.md`.

- `graph.py` — el `StateGraph` propio (sin prebuilt) y sus nodos
- `state.py` — estado compartido; los acumuladores se resetean por run
- `runner.py` — ciclo de vida del run: eventos, cancelación, timeout, persistencia
- `events.py` — eventos del protocolo AG-UI y su serialización SSE
- `prompts.py` — system prompt (incluye la regla de datos vs. instrucciones)
- `compact.py` — resumen del historial que el recorte deja afuera
- `llm.py` — ChatOllama con `num_ctx`/`num_predict` explícitos y chequeo de salud

La compactación **no es un nodo** del grafo: corre dentro de `retrieve_context`
cuando el recorte descarta mensajes, y emite su propio step `compact` para la UI.
El resumen no viaja en el historial (volvería a entrar en el recorte y se
degradaría): vive en `CONVERSATIONS.summary` y `agent_node` lo inyecta cada turno.
