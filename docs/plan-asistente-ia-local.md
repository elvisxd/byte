# Byte — Agente de IA Local estilo Claude (self-hosted)
## Plan de Acción: ambicioso pero escalable (v6, con revisión de seguridad)

## Visión (objetivo final)
Un agente de IA propio, corriendo un modelo open source local (sin depender de APIs pagas), capaz de programar, buscar en internet y usar herramientas vía MCP — desplegado en Railway, con interfaz web propia y un cliente CLI en Go. Portafolio: Python + FastAPI + arquitectura de agentes + MCP + IA aplicada + despliegue en la nube. Distinto de los proyectos de trading, afiliados de Amazon y apuestas que ya tengo.

## Identidad
- Nombre: **Byte**
- Mascota: perrito geométrico flat con etiqueta `</>` ámbar en el collar
- Paleta: azul marino #3B4A6B, azul oscuro #2E3A57, crema #F4EDE4, ámbar #F5A524, casi negro #1A1F2E
- Tipografía sans-serif moderna (Inter/Manrope); monoespaciada para el CLI
- Diseños en Canva: presentación de 3 pantallas (chat, historial, documentos) ya guardada; pantalla del CLI generada. Prompts reutilizables en `prompts-canva-byte.md`

## Principio de arquitectura: módulos intercambiables, no todo junto
- El **núcleo del agente** (loop de razonamiento + FastAPI) es la única parte que se construye desde el día uno
- Cada herramienta (búsqueda web, ejecución de código, RAG, MCP) se conecta al núcleo como un módulo separado con interfaz clara — se agrega, saca o cambia sin tocar el resto
- El modelo (Ollama + Qwen3-Coder) también es intercambiable
- Cada carpeta del repo es un módulo independiente (ver estructura más abajo)

## Stack tecnológico (visión completa)
- **Backend:** FastAPI
- **Modelo:** Qwen3-Coder-30B-A3B-Instruct — MoE (30B totales, ~3B activos por token), tool-calling funcional, contexto 256K, Apache 2.0, ~18GB cuantizado (Q4_K_M), viable en CPU
- **Runtime del modelo:** Ollama — setear `num_ctx` explícitamente (por defecto arranca en 4.096 tokens aunque el modelo soporte 256K). La KV cache consume RAM extra: los 18GB son solo pesos; con 16-32K de contexto sumar 2-6GB más. Medir, no asumir
- **Embeddings:** `nomic-embed-text` (768 dimensiones) — DECIDIDO ANTES de crear la tabla de chunks, porque la dimensión queda fija en el esquema y cambiar de modelo después obliga a re-indexar todo
- **Framework de agente:** LangGraph (único; CrewAI descartado)
- **Herramientas del agente:**
  - Búsqueda web: **Tavily** para el MVP (API gratuita hasta ~1.000 búsquedas/mes, hecha para agentes). SearxNG self-hosted queda como opción a evaluar después — desde IPs de datacenter como Railway suele ser bloqueado o pedir captcha
  - Ejecución de código: **sandbox WebAssembly con Pyodide** — servicio chico en Node/Deno que ejecuta Python dentro de un runtime WASM (memoria aislada, sin acceso al sistema, no necesita permisos especiales). Alternativa si hace falta un Linux completo: **E2B** (plan gratuito). DESCARTADOS: Docker-in-Docker y Piston — ambos necesitan modo privilegiado y Railway lo prohíbe (además la API pública de Piston dejó de ser gratuita en feb 2026)
- **RAG:** PostgreSQL con imagen `pgvector/pgvector` (el Postgres por defecto de Railway puede no traer la extensión) + índice HNSW + búsqueda híbrida (vector + tsvector). Chunking: ~500 tokens con 50 de solapamiento
- **MCP:** protocolo estándar para conectar herramientas externas al agente
- **n8n (opcional, periférico):** conectado vía MCP para ingesta automática de documentos al RAG y canales externos (WhatsApp, email). No reemplaza el razonamiento del agente
- **Interfaces:**
  - MVP: una página HTML mínima servida por FastAPI (Jinja) — se arma igual de rápido que Streamlit y no es trabajo tirado. Streamlit DESCARTADO
  - Final (Fase 5): **FastAPI + Jinja + HTMX** (o React si se quiere sumar al CV) con el diseño de Canva
  - Streaming de respuestas con **SSE (Server-Sent Events)** desde el MVP — lo necesitan el indicador de "escribiendo" y el CLI
  - CLI en Go como v2
- **Debugging y observabilidad:**
  - `structlog` para logging estructurado
  - **Bugsink** para tracking de errores — self-hosted, un solo contenedor, ~1GB RAM, compatible con SDK de Sentry
  - **Langfuse Cloud (plan gratuito Hobby)** para trazas del agente. NO self-hosted: la v3 necesita Postgres + ClickHouse + Redis + S3, contradice la meta de RAM mínima. Tradeoff a explicitar en el README: los prompts salen a un tercero; se puede self-hostear después
- **Seguridad (detalle en `seguridad-byte.md`, basado en OWASP LLM 2025 + Agentic 2026):**
  - Solo FastAPI con dominio público; **Ollama, Postgres y sandbox solo en red privada** (Ollama no tiene auth y su API permite borrar/descargar modelos)
  - Web con **sesión por cookie `httpOnly`** — `EventSource` no puede mandar headers y la API key nunca va en la URL. CLI con header `X-API-Key`
  - API key comparada en tiempo constante y guardada hasheada; rate limiting con `slowapi`; máx. 1-2 runs concurrentes; `num_predict` acotado; timeout por run; límite de gasto en Railway
  - **Inyección indirecta de prompts:** resultados de herramientas y chunks envueltos y marcados como datos no confiables, con tamaño acotado; queries de búsqueda con límite; sin herramienta de URL arbitraria en el MVP; **modo seguro automático** si en un mismo run hubo web + ejecución de código
  - Salida del modelo sanitizada (Markdown → HTML con `nh3`), cabeceras CSP/HSTS, CORS restringido; nunca `eval` de salida del modelo
  - Sandbox sin secretos, sin red, intérprete nuevo por ejecución
  - Multi-usuario (Fase 4): filtro por `user_id` en toda consulta incl. pgvector y runs (IDOR); argon2; JWT corto; `resume_token` firmado de un solo uso
  - Redacción de PII/secretos antes de enviar a Langfuse; `gitleaks` + `pip-audit` en CI; Dependabot
- **Calidad del repo:** GitHub Actions con `ruff` + `pytest`, `pre-commit`, `.env.example` (nunca commitear `.env`), licencia MIT
- **Despliegue:** Railway (plan Pro), con foco en RAM mínima. Volúmenes persistentes para Ollama (~20GB, ~$3/mes) y para el SQLite de Bugsink

## Estructura de carpetas (un módulo por carpeta)
```
byte/
├── api/            # FastAPI: rutas, esquemas de entrada/salida
├── agent/          # Núcleo del agente (LangGraph): grafo, nodos, estado
├── tools/          # Herramientas que el agente puede usar
│   ├── web_search.py   # Tavily (SearxNG después, si aplica)
│   └── code_exec.py    # Cliente del sandbox WASM (Pyodide)
├── sandbox/        # Servicio Node/Deno con Pyodide para ejecutar código aislado
├── mcp_client/     # Cliente MCP (no `mcp/`: ese es el nombre del paquete del SDK)
├── rag/            # Ingesta de documentos, embeddings, búsqueda híbrida
├── models/         # Esquemas de datos (Pydantic / SQLAlchemy)
├── db/             # Conexión y migraciones de PostgreSQL + pgvector
├── web/            # Frontend (HTML mínimo con Jinja en MVP → Jinja+HTMX o React después)
├── cli/            # Cliente en Go (carpeta aparte por ser otro lenguaje)
├── n8n/            # Workflows exportados (JSON): ingesta RAG, canales externos
├── docker/         # Dockerfiles de cada servicio
├── tests/          # Tests con pytest
├── evals/          # Set chico de ~10 tareas para evaluar al agente y detectar regresiones
├── .github/        # GitHub Actions (ruff + pytest)
└── docs/           # README, diagramas, decisiones técnicas
```
(Se eliminó `skills/`: el concepto estaba vago y MCP ya cubre lo que haría. Se agrega si alguna vez hace falta.)

## Base de datos (ERD v3)
Tablas: USERS, CONVERSATIONS, MESSAGES, DOCUMENTS, DOCUMENT_CHUNKS.
- **USERS**: id, email, password_hash, created_at
- **CONVERSATIONS**: id, user_id, title, created_at, **updated_at** (para agrupar el sidebar en Hoy / Ayer / Esta semana), **summary** (text, resumen compactado del historial viejo) y **summary_up_to_message_id** (hasta qué mensaje cubre el resumen)
- **MESSAGES**: id, conversation_id, role, content, **metadata jsonb** (fuentes citadas del RAG, resumen de herramientas usadas — lo que la UI muestra), langfuse_trace_id, created_at
- **DOCUMENTS**: id, user_id, filename, **status** (processing / indexed / error — lo que muestra la pantalla de carga), **size_bytes**, **mime_type**, uploaded_at
- **DOCUMENT_CHUNKS**: id, document_id, chunk_index, content, embedding vector(768), content_search tsvector
- Sin tabla propia de tool calls: esa data la traza Langfuse; `MESSAGES.langfuse_trace_id` linkea con la traza completa
- El checkpointer de LangGraph (`PostgresSaver`) crea sus propias tablas de estado del grafo en el mismo Postgres. `MESSAGES` sigue siendo la fuente de verdad para la UI; las tablas del checkpointer son estado interno del agente
- Índice HNSW sobre `embedding`; búsqueda híbrida vector + tsvector
- **Verificado contra Postgres 16 real** (repositorio, migración y checkpointer),
  no solo contra la implementación en memoria: los tests corren contra las dos

## Estrategia de RAM y costos
Railway cobra por uso (~$10/GB RAM al mes, prorrateado por segundo). 18-20GB de RAM 24/7 = ~$180-200/mes solo en RAM. Por eso:
- **Arrancar con la menor RAM posible:** validar la arquitectura con un modelo chico (Qwen2.5-Coder 7B) antes de subir al 30B-A3B
- **Ojo con scale-to-zero:** cargar 18GB a RAM en cada "despertar" tarda varios minutos — inaceptable para una demo en entrevista. Estrategia real: **prender el servicio de Ollama manualmente ~10 min antes de una demo y apagarlo después**, y/o mantener un modelo chico siempre tibio para demos rápidas
- **Desarrollo del día a día:** correr el modelo gratis en la PC (16GB+ RAM) y reservar Railway para el deploy final/demo
- **Cada servicio extra cuesta RAM:** por eso Langfuse va en Cloud gratis, Bugsink es de 1 contenedor, y n8n/SearxNG son opcionales
- **Escalar más adelante:** si el proyecto crece, agregar un panel simple para subir/bajar la RAM asignada según necesidad

## MVP (Fase 0) — lo único que se construye antes de todo lo demás
Objetivo: un agente funcionando de punta a punta, chico pero real. Sin RAG, MCP, n8n, CLI en Go, JWT ni diseño pulido todavía. Estimación realista aprendiendo Python en paralelo: **2-4 semanas a tiempo parcial**.
- [ ] Ollama local + Qwen2.5-Coder 7B (validar), después Qwen3-Coder-30B-A3B; setear `num_ctx` y medir RAM real — **pendiente: el código está listo y setea `num_ctx`/`num_predict`, falta correrlo contra un Ollama real**
- [x] Backend FastAPI mínimo según el contrato: `POST /conversations/{id}/messages` → `GET /runs/{id}/events` (SSE con eventos AG-UI), `POST /runs/{id}/cancel`, `/health` y `/health/details`
- [x] Logging estructurado con `structlog` desde el día uno
- [x] Agente con LangGraph con **una sola herramienta**: búsqueda web vía Tavily
- [x] Página HTML mínima servida por FastAPI (sin diseño todavía)
- [x] Checklist de seguridad Fase 0 de `seguridad-byte.md`: servicios internos sin dominio público, API key hasheada + cookie httpOnly para la web, rate limiting, límites de tokens/iteraciones/concurrencia, delimitación de resultados de herramientas, cabeceras de seguridad, `gitleaks` en CI — **falta solo el límite de gasto en Railway (se configura en el dashboard); la sanitización de Markdown con `nh3` no aplica hasta la Fase 5: la página mínima pinta con `textContent`**
- [ ] Deploy de prueba en Railway con la menor RAM posible (con volumen para Ollama), medir costo real — **pendiente**
- [x] GitHub con README básico, estructura de carpetas, CI (ruff + pytest + gitleaks + pip-audit), `.env.example`, licencia MIT

## Fases de escalado (después del MVP, en orden)

### Fase 1 — Segunda herramienta: ejecución de código (estimación: 1-2 semanas)
- [~] Servicio `sandbox/` en Node con Pyodide (WASM) — **construido y con 25 tests propios; el deploy en Railway queda para la Fase 7.** Se eligió Node sobre Deno por `worker_threads` con límites de memoria y porque es lo que se pudo verificar de punta a punta
- [x] `tools/code_exec.py` como cliente de ese sandbox, con timeout y límite de salida
- [x] Endurecer el sandbox según `seguridad-byte.md` (sin red, sin secretos, intérprete nuevo por ejecución) y modo seguro automático cuando hubo web + código en el mismo run
- [ ] Probar el agente resolviendo tareas reales de programación — **pendiente: necesita Ollama corriendo. Los evals están listos para eso**
- [x] Armar `evals/` con ~10 tareas de prueba

### Fase 2 — Memoria y RAG (estimación: 2-3 semanas)
- [x] Postgres con imagen `pgvector/pgvector`; índice HNSW; tabla `DOCUMENT_CHUNKS` con vector(768) para `nomic-embed-text`
- [x] Chunking ~500 tokens / 50 de solapamiento
- [x] Ingesta de documentos con estado (processing / indexed / error); parseo con timeout; chunks marcados como no confiables; las consultas de `rag/store.py` ya filtran por `user_id`, pero **todos los call sites le pasan `None`**: el filtro existe y nunca discrimina hasta que la Fase 4 traiga el usuario real. `db/repository.py` todavía no tiene el parámetro
- [x] Búsqueda híbrida (vector + tsvector) y citas de fuentes en `MESSAGES.metadata`
- [x] Nodo `compact`: cuando el historial supera ~60% del `num_ctx`, resume los mensajes viejos y guarda `CONVERSATIONS.summary`; `retrieve_context` pasa a cargar resumen + últimos N mensajes
- [x] El agente "recuerda" documentos previos vía RAG; la memoria dentro de una conversación es el checkpointer + la compactación (memoria entre conversaciones distintas queda como opción futura)

### Fase 3 — MCP y n8n (estimación: 2 semanas)
- [x] Soporte de MCP para conectar herramientas externas de forma estandarizada (solo servidores propios o revisados: tool poisoning). Cliente en `mcp_client/`: los servidores se declaran en `BYTE_MCP_SERVERS` (lista blanca, solo http(s)) y sus herramientas entran al registro junto a las nativas, con `source: "mcp:<nombre>"`. La carpeta se llama `mcp_client` y no `mcp` porque ese nombre es el del paquete oficial del SDK. Falta el servidor MCP (exponer las herramientas de Byte), que el plan no pedía
- [x] Endpoint compatible con OpenAI con lista blanca de modelos: `POST /v1/chat/completions` (con y sin streaming) y `GET /v1/models`, montados en `/v1` porque los clientes arman la URL pegando la ruta a la base. Autentica con `Authorization: Bearer`. Verificado con el SDK oficial de OpenAI
- [x] Sumar n8n como módulo opcional conectado por MCP: ingesta automática de documentos y un canal externo (WhatsApp o email). Tres workflows en `n8n/`: ingesta al RAG, canal de email (se eligió sobre WhatsApp: no necesita cuenta de empresa ni aprobación de Meta) y n8n como servidor MCP. Servicio en el compose bajo el perfil `n8n`. Probado con n8n 2.38.7: importan sin errores y Byte usa una herramienta de n8n de punta a punta

### Fase 4 — API completa, debugging y observabilidad (estimación: 2 semanas)
- [x] Autenticación JWT multi-usuario (argon2, expiración corta) y aislamiento por `user_id` en todas las consultas y endpoints de runs. `POST /auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout` y `GET /me`. Contraseñas con **argon2id** (rehash automático en el login); **access token de 30 min** y **refresh de 14 días con rotación y detección de reuso**. El `user_id` del JWT reemplazó a `SIN_USUARIO` en las 15 llamadas: un usuario ve lo suyo y recibe 404 en lo ajeno (404 y no 403, para no confirmar que el id existe). La API key sigue siendo la instancia —su dueño es `None`— así que lo de antes del multi-usuario sigue siendo suyo
- [x] Redacción de PII/secretos antes de enviar trazas a Langfuse. `api/redaccion.py`: claves con prefijo conocido (OpenAI, GitHub, Slack, AWS, Google), JWT, DSN con credenciales, emails y tarjetas (validadas con Luhn, para no redactar cualquier id largo). Va como `mask` del cliente de Langfuse y `before_send` de Sentry, así que corre sobre **todo** lo que esos SDK están por mandar y no depende de acordarse en cada punto
- [x] Bugsink self-hosted (1 contenedor) para tracking de errores, bajo el perfil `observabilidad` del compose. Reusa el Postgres que ya está —su documentación desaconseja persistir SQLite en un volumen Docker por el modo WAL— y con `PHONEHOME=false`: lo que se instala local no tiene por qué avisarle a nadie
- [x] Langfuse para trazas del agente, guardando `langfuse_trace_id` en `MESSAGES`. **Opcional y apagado por defecto**: sin `LANGFUSE_PUBLIC_KEY` no sale nada de tu máquina. La traza envuelve el run entero, no cada llamada al modelo: lo que interesa al depurar es por qué el agente decidió lo que decidió. Va en el extra `observabilidad` para no pesarle a quien no lo use
- [ ] Ampliar la cobertura de tests (el CI con pytest existe desde el MVP)

**Lo que dejó anotado la revisión de la Fase 3** (hacerlo *antes* del JWT sale
mucho más barato que después):

- [x] **`credential_id` deja de derivarse de la clave.** Se deriva con HMAC bajo `BYTE_SECRET_KEY` —el mismo secreto que firma la cookie y el `resume_token`— así que sigue siendo estable pero ya no dice nada de la API key. Lo que sigue abajo era el porqué: Hoy son los primeros 12
  hex del SHA-256 **sin sal** de `BYTE_API_KEY` (`api/security.py:43`), y viaja
  al cliente en la cookie de sesión y dentro del `resume_token`, que la API
  devuelve en un cuerpo JSON. Contra una clave generada con `openssl rand` no
  importa; contra una elegida a mano —o contra el `cambiame` del `.env.example`—
  es un diccionario offline con oráculo de confirmación. Y ya está cementado en
  el formato de los tokens: cuando pase a ser el identificador de tenant, cada
  usuario publicaría 48 bits del hash de su credencial en cada token. Tiene que
  ser un id opaco o el `user_id` de la base.
- [x] **Bajar el dueño al `Repository`.** `create_conversation`, `get_conversation`, `list_conversations`, `set_title`, `delete_conversation` y `get_message` toman `user_id`, con el mismo `IS NOT DISTINCT FROM` del RAG; `messages` filtra por JOIN con su conversación, sin columna propia. Las rutas pasan `SIN_USUARIO` explícito, así que buscar la constante da los puntos que el JWT tiene que cambiar. Seis tests de aislamiento corren contra memoria **y contra Postgres**. Lo que falta es el mapeo credencial → usuario, que llega con los usuarios reales. Lo de abajo era el porqué: `rag/store.py` ya filtra por `user_id`
  en todas sus consultas; `db/repository.py` no tiene el parámetro, y
  `CONVERSATIONS`/`MESSAGES` no tienen la columna. Todo `api/routes/conversations.py`
  recibe la credencial como `_credential` —exige autenticación y descarta la
  identidad—, así que con multi-usuario cada una de esas rutas es un IDOR. El
  modelo a copiar está en `RunManager.require` (`agent/runner.py:191`), que
  compara el dueño y responde 404 para no permitir enumeración.
- [ ] **Decidir qué pasa con `Authorization: Bearer`.** Desde la Fase 3 acepta
  la API key estática, que es lo único que mandan los clientes de OpenAI y lo
  que n8n guarda en sus credenciales. El contrato ya planea un JWT en el mismo
  header: van a convivir un token de 15-60 min y una clave eterna, y hay que
  decidir explícitamente si la clave sigue valiendo para `/v1` o si esos
  clientes pasan a un token de servicio aparte.
- [ ] **Política de retención para las conversaciones de `/v1`.** Cada pedido
  crea una, y no se borran: con el canal de email de n8n activo, cada correo
  entrante deja una permanente. Además el título lleva los primeros 60
  caracteres del mensaje, así que la redacción de PII para Langfuse tiene que
  cubrir `CONVERSATIONS.title`, no solo el cuerpo.
- [ ] **`resume_token` y `events_token` de un solo uso solo valen con un worker.**
  Los nonces gastados viven en un dict de proceso (`api/security.py:70`), igual
  que los runs (`agent/runner.py:186`). Con más de un worker el mismo token se
  canjea una vez por cada uno, y `require()` no encuentra los runs de otro
  proceso.

### Fase 5 — Frontend real con la identidad Byte (estimación: 2-3 semanas)
- [x] Evolucionar la página HTML mínima a FastAPI + Jinja con la identidad Byte, consumiendo el streaming SSE (sin HTMX: el chat ya tenía resuelta la reconexión con `Last-Event-ID`)
- [x] Las pantallas diseñadas en Canva: chat, historial agrupado por `updated_at` (Hoy / Ayer / Esta semana) en la sidebar, y carga de documentos con arrastrar y soltar y `status` en etiquetas. Sin barra de progreso: la ingesta no reporta avance parcial, así que el estado es de tres valores
- [x] Indicador de escritura animado (respeta `prefers-reduced-motion`) y estado "En línea" leyendo `/health/details` cada 30 s
- [x] Marcador visual de "conversación compactada" en el chat, desde `summary_up_to_message_id`

### Fase 6 — CLI en Go (v2) (estimación: depende del aprendizaje de Go)
- [ ] Sacar el certificado/base de Go
- [ ] Cliente CLI en Go que consuma la misma API FastAPI, siguiendo la pantalla diseñada en Canva (`byte ask`, `byte search`, `byte run`)
- [x] Mientras tanto, un CLI en Python con los mismos comandos (`cli/byte_cli.py`): la API ya estaba completa, así que esperar a Go dejaba la terminal sin usar sin ninguna razón técnica

### Fase 7 — Despliegue final optimizado (estimación: 1 semana)
- [ ] Servicios separados en Railway por red privada: Ollama, FastAPI, Postgres, sandbox WASM, Bugsink (+ n8n si se usa), con sus volúmenes
- [ ] Rutina de demo: encender Ollama antes, apagar después
- [ ] Medir costo real una semana antes de dejarlo fijo
- [ ] (Si se justifica) panel para ajustar RAM según demanda

### Fase 8 — Documentación y portafolio (estimación: 1 semana)
- [ ] README con arquitectura, decisiones técnicas y cómo correrlo; banner y logo de Canva
- [ ] Diagrama del flujo completo + diagrama automático con GitDiagram (gitdiagram.com/usuario/repo)
- [ ] Explicación corta para entrevistas: problema que resuelve, decisiones de costo/arquitectura, qué bugs de diseño se detectaron y cómo se resolvieron (sandbox WASM vs Docker/Piston privilegiados, Langfuse Cloud vs self-hosted, cold start del modelo, KV cache, pgvector en Railway)

## Conectores de Claude relevantes
- **Railway:** conectado — se puede revisar deploys y logs desde el chat
- **Canva:** conectado — se usó para las pantallas; prompts en `prompts-canva-byte.md`

## Documentos del proyecto
`plan-asistente-ia-local.md` (este) · `api-contrato-byte.md` · `seguridad-byte.md` · `prompts-canva-byte.md` — todos en `docs/` del repo
- **GitHub:** sin conector nativo; el repo se maneja por terminal
- **Datadog:** disponible pero overkill; se prioriza Bugsink + Langfuse Cloud

## Decisiones ya tomadas
- Backend: **FastAPI**
- Modelo: **Qwen3-Coder-30B-A3B-Instruct**; embeddings **nomic-embed-text (768)**
- Agente: **LangGraph** (CrewAI descartado), arquitectura modular
- Búsqueda web: **Tavily** en MVP (SearxNG a evaluar después)
- Ejecución de código: **sandbox WASM con Pyodide** como servicio (Docker-in-Docker y Piston descartados: Railway prohíbe modo privilegiado)
- Observabilidad: **Bugsink** self-hosted + **Langfuse Cloud** gratuito
- Frontend: HTML mínimo con Jinja en MVP → **FastAPI + Jinja + HTMX** (o React) al final; Streamlit descartado. Streaming SSE desde el MVP
- Automatización periférica: **n8n** opcional vía MCP
- Nube: **Railway** (Pro), RAM mínima, encendido manual del modelo para demos, volúmenes persistentes, límite de gasto configurado
- Seguridad: API key + rate limiting desde el primer deploy; JWT en Fase 4
- API: runs como recurso + SSE con eventos **AG-UI**; compactación de historial con resumen guardado en la conversación
- Calidad: CI con ruff + pytest, pre-commit, evals del agente
- Interfaz principal: web; CLI en **Go** como v2
- Identidad: **Byte**, mascota perrito con etiqueta `</>`, paleta definida
- Estructura de carpetas: un módulo por carpeta (sin `skills/`)
- Metodología: MVP primero, después escalar en capas sin rehacer lo anterior

## Flujo del agente (LangGraph) — v2 revisado contra prácticas 2026
Patrón ReAct con `StateGraph` propio (no el prebuilt, para transparencia y control). Nodos: `retrieve_context` (recortar historial con `trim_messages` + RAG; en el MVP solo historial) → `agent` (LLM con herramientas) → decisión `should_continue` → si pidió herramienta va a `tools` y el resultado vuelve a `agent` (loop); si no, `finalize` (streaming SSE, guardar en Postgres con `langfuse_trace_id`, cerrar traza).
- **Estado tipado explícito:** `messages`, `retrieved_chunks`, `iteration_count`, `tool_results`
- **Checkpointer `PostgresSaver`** (nunca in-memory en producción) con `thread_id = conversation_id`: cada conversación retoma donde quedó, habilita time-travel debugging y los interrupts
- **Errores de herramientas como mensajes:** `ToolNode(handle_tool_errors=True)` — el LLM ve el error y prueba otra estrategia en vez de crashear. `RetryPolicy` en nodos con fallos transitorios (Ollama, Tavily, sandbox)
- **Máximo de iteraciones** del loop (ej. 6) para evitar loops infinitos
- **Recorte y compactación del historial:** ventana deslizante de los últimos N mensajes desde el MVP. En Fase 2, nodo `compact`: si el historial supera ~60% del `num_ctx`, el modelo resume los mensajes viejos, se guarda en `CONVERSATIONS.summary` y `retrieve_context` carga resumen + últimos N. Los mensajes originales no se borran (la UI los sigue mostrando), solo dejan de enviarse al modelo. También disparable a mano con `POST /conversations/{id}/compact`
- **"Modo seguro" opcional:** `interrupt()` antes de ejecutar código, activable desde la UI (Fase 3/4)
- **Streaming doble:** tokens (`stream_mode="messages"`) + eventos del grafo (`"updates"`), traducidos a eventos **AG-UI** (`TEXT_MESSAGE_CONTENT`, `STEP_STARTED`, `TOOL_CALL_START`…) para las líneas de estado de la UI y el CLI
- Descartado: nodo de clasificación de intención al inicio — una llamada extra al LLM en CPU sin beneficio para un agente general
- Agregar una herramienta = registrarla en `tools/`; el grafo no cambia

## Contrato de la API (v2)
Definido en `api-contrato-byte.md`. Puntos clave:
- Prefijo `/api/v1`; API key en MVP → JWT en Fase 4; errores JSON uniformes; `Idempotency-Key`; rate limits más estrictos en `/runs` y `/execute`
- **Runs como recurso:** `POST /conversations/{id}/messages` devuelve `202 { run_id }` y el stream va por `GET /runs/{id}/events` (SSE). Obligatorio porque `EventSource`/HTMX solo soportan GET; habilita `Last-Event-ID` (reconexión), `POST /runs/{id}/cancel` (botón detener, evita quemar CPU) y `POST /runs/{id}/resume` (modo seguro)
- **Eventos según AG-UI** (Agent-User Interaction Protocol): estándar abierto sobre SSE con integración de primera clase en LangGraph; completa el stack MCP (herramientas) + A2A (agentes) + AG-UI (usuario)
- Headers anti-buffering en SSE (`Cache-Control: no-cache`, `X-Accel-Buffering: no`, sin gzip) — verificar en Railway el primer día
- Documentos asíncronos (202, validados por magic bytes), `/search` híbrida, `/tools`, `/execute` con límite de 50 KB
- Borrar conversación borra también el hilo del checkpointer
- **Opcional Fase 3:** endpoint compatible con OpenAI (`/v1/chat/completions`) para usar Byte desde Open WebUI, Continue.dev o cualquier SDK de OpenAI
- **Opcional Fase 5:** capa GraphQL de solo lectura con Strawberry (patrón híbrido); el núcleo queda en REST + SSE para no salirse del ecosistema de IA
- El CLI en Go genera su cliente tipado desde el OpenAPI con `oapi-codegen`

## Pendiente de decidir más adelante
- Jinja+HTMX vs React para el frontend final
- Si se implementa la capa GraphQL (Strawberry) en Fase 5 y el endpoint compatible con OpenAI en Fase 3
- Sandbox WASM (Pyodide) vs E2B si se necesita un Linux completo
- SearxNG: si vale la pena sumarlo después de Tavily
- Interfaz de WhatsApp: sí o no, y si va directo o vía n8n
- Diseño final del panel de escalado de RAM en Railway
