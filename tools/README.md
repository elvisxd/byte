# tools/
Herramientas que el agente puede usar. Una herramienta = un módulo. Agregar una herramienta no cambia el grafo.
- `base.py` — registro de herramientas y el envoltorio de contenido no confiable
- `web_search.py` — Tavily (SearxNG a evaluar después)
- `code_exec.py` — cliente del sandbox WASM (Pyodide) — Fase 1

Cada herramienta declara su esquema Pydantic: los argumentos que manda el modelo
se validan antes de ejecutar nada.
