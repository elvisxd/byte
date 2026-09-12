# tools/
Herramientas que el agente puede usar. Una herramienta = un módulo. Agregar una herramienta no cambia el grafo.
- `base.py` — registro de herramientas y el envoltorio de contenido no confiable
- `web_search.py` — Tavily (SearxNG a evaluar después)
- `code_exec.py` — cliente del sandbox WASM (Pyodide), más el chequeo de salud
  que alimenta `/health/details`
- `doc_search.py` — búsqueda híbrida en los documentos del usuario (RAG)
- `registry.py` — arma el registro según la configuración: cada herramienta se
  registra solo si está configurada

Cada herramienta declara su esquema Pydantic: los argumentos que manda el modelo
se validan antes de ejecutar nada.

## `archivos.py` — list_files, read_file, grep

Lo que separa a un asistente que habla de código de uno que lo mira. Opt-in:
sin `BYTE_PROJECT_ROOT` las tres herramientas no se registran y el agente no ve
el disco.

Todo cuelga de esa raíz y **salirse es imposible**: las rutas se comparan
después de `resolve()`, que sigue los symlinks, así que ni `../..` ni un enlace
plantado llegan afuera. No es teórico — el modelo elige las rutas a partir de
lo que leyó, y lo que leyó puede ser un README con instrucciones adentro.

Tres límites más, cada uno por algo que rompe en la práctica: lo binario no se
lee (entra como basura al prompt), los archivos se leen por trozos con sus
números de línea y se avisa cuánto quedó afuera (recortar en silencio hace que
el modelo razone sobre la mitad creyendo que la tiene entera), y `.git`,
`node_modules` y compañía no se listan.
