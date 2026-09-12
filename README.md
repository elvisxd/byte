# Byte — Agente de IA local (self-hosted)

> Un agente de IA propio que corre un modelo open source localmente, capaz de programar, buscar en internet y usar herramientas vía MCP — sin depender de APIs pagas.

**Estado:** MVP (Fase 0), ejecución de código en sandbox (Fase 1) y memoria con RAG (Fase 2) funcionando de punta a punta en local.

## Stack
Python · FastAPI · LangGraph · Ollama (Qwen3-Coder-30B-A3B) · PostgreSQL + pgvector · MCP · AG-UI · Railway · Go (CLI)

## Arquitectura en una línea
`web / CLI` → `FastAPI (runs + SSE con eventos AG-UI)` → `agente LangGraph (checkpointer en Postgres)` → herramientas: `búsqueda web (Tavily)`, `sandbox WASM (Pyodide)`, `RAG híbrido (pgvector)`, `MCP (n8n y otros)`.

## Documentación
- [Plan de acción y arquitectura](docs/plan-asistente-ia-local.md) — visión, stack, estructura, ERD, flujo del agente, fases, decisiones y bugs de diseño detectados
- [Contrato de la API](docs/api-contrato-byte.md) — endpoints, runs, eventos AG-UI, documentos, sandbox
- [Seguridad: modelo de amenazas y checklist](docs/seguridad-byte.md) — OWASP LLM 2025 + Agentic 2026
- [Identidad visual y prompts de Canva](docs/prompts-canva-byte.md)
- [Diagramas](docs/diagramas/) — el sistema completo y las conexiones con n8n, como páginas que se abren en el navegador

## Estructura
Un módulo por carpeta; cada carpeta tiene su README explicando qué va ahí.

```
api/  agent/  tools/  sandbox/  mcp_client/  rag/  models/  db/  web/  cli/  n8n/  docker/  tests/  evals/  docs/
```

## Cómo correrlo

Hace falta [uv](https://docs.astral.sh/uv/) y [Ollama](https://ollama.com) (local o en Docker).

```bash
# 1. Dependencias (crea el venv con Python 3.12 desde uv.lock)
uv sync

# 2. Configuración
cp .env.example .env
# Editar .env: como mínimo BYTE_API_KEY y BYTE_SECRET_KEY
#   openssl rand -hex 32   (una para cada una)

# 3. El modelo (gratis, en tu PC)
ollama pull qwen2.5-coder:7b

# 4. El sandbox de ejecución de código (en otra terminal)
cd sandbox && npm install
SANDBOX_TOKEN=$(openssl rand -hex 32) npm start   # el mismo token va en .env
cd ..

# 5. Postgres con pgvector (opcional; sin él todo queda en memoria)
# El --env-file es necesario: con -f, Compose busca el .env junto al compose
# (en docker/), no en la raíz, y la interpolación de SANDBOX_TOKEN falla.
docker compose --env-file .env -f docker/docker-compose.yml up -d postgres ollama

# 6. Levantar la API
uv run uvicorn api.main:app --reload
```

- Chat mínimo: http://localhost:8000 (pide la API key y la canjea por una cookie)
- API y OpenAPI: http://localhost:8000/docs
- Salud: `curl localhost:8000/api/v1/health`

En CPU, un 7B hace unos 5-15 tokens por segundo y un run son varias llamadas al
modelo: por eso `BYTE_RUN_TIMEOUT_S` viene en 600. Si ves runs que se cortan
solos, ese es el primer lugar donde mirar.

Sin `DATABASE_URL`, Byte arranca en memoria: sirve para probar, pero las
conversaciones se pierden al reiniciar (lo avisa en el log). Con `BYTE_ENV=prod`
directamente no arranca sin Postgres. Sin `TAVILY_API_KEY` el agente funciona
igual pero sin búsqueda web, y sin `SANDBOX_URL`/`SANDBOX_TOKEN`, sin ejecutar
código.

### Probar sin navegador

```bash
KEY=tu-api-key
CONV=$(curl -s -X POST localhost:8000/api/v1/conversations \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{}' | jq -r .id)

# ?wait=true espera la respuesta completa en un solo JSON
curl -s -X POST "localhost:8000/api/v1/conversations/$CONV/messages?wait=true" \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"content":"buscá la última versión de FastAPI"}' | jq
```

### Tests y calidad

```bash
uv run pytest -q          # no necesita Ollama, Tavily ni Postgres

# Con Postgres levantado, se suman los tests del repositorio y el checkpointer
# contra la base real (en CI esto corre siempre)
BYTE_TEST_DATABASE_URL=postgresql://byte:byte@localhost:5432/byte uv run pytest -q

uv run ruff check .
uv run ruff format .
uv run pre-commit install # ruff + gitleaks antes de cada commit

cd sandbox && npm test    # incluye la suite de escape del sandbox
```

## Qué hay hoy

### Fase 4 (en curso) — usuarios con JWT

- **`POST /auth/register`, `POST /auth/login`, `GET /me`**: contraseñas con
  **argon2id** y un JWT de 30 minutos. El token va en `Authorization: Bearer`,
  el mismo header que la API key —se prueba primero el JWT y después la clave—,
  así que los clientes de OpenAI y n8n siguen funcionando sin cambios
- **El login no dice si un email existe**: mismo mensaje y mismo tiempo ante un
  email desconocido y una contraseña incorrecta, para que no se pueda enumerar
  quién está registrado
- **El filtro por dueño está en las tres capas** (documentos, conversaciones y
  runs), con tests de aislamiento que corren también contra Postgres

Falta que el usuario del JWT reemplace a `SIN_USUARIO` en las rutas: hoy alguien
se autentica, pero las consultas siguen pasando la constante. Y falta el
refresh: el token vence a los 30 minutos y hay que volver a entrar.

### Fase 3 — herramientas externas por MCP

- **Cliente MCP** (`mcp_client/`): los servidores se declaran en
  `BYTE_MCP_SERVERS` como `nombre=url` y sus herramientas entran al registro del
  agente junto a las nativas, con `source: "mcp:<nombre>"`. Un servidor caído no
  impide arrancar, igual que Byte arranca sin Tavily o sin sandbox
- **Lista blanca**: el modelo no elige a qué host se conecta Byte. Solo http(s):
  `stdio` implicaría lanzar procesos, que es otra superficie de ataque
- **Contra el tool poisoning**: los argumentos se validan contra el esquema que
  declara el servidor antes de ejecutar; el resultado entra al prompt marcado
  como contenido no confiable; la descripción se sanea y se atribuye
  (`[servidor MCP 'x'] …`); y una herramienta externa no puede tapar a una
  nativa —`code_exec` sigue siendo el sandbox de Byte
- **La descripción no se envuelve** en los delimitadores, a diferencia del
  resultado: medido contra qwen3:8b, envolverla rompe el tool calling (0 de 3
  llamadas contra 3 de 3). El porqué está en `mcp_client/README.md`

- **Compatible con OpenAI**: `POST /v1/chat/completions` (con y sin streaming) y
  `GET /v1/models`. Open WebUI, Continue.dev o cualquier SDK de OpenAI usan a
  Byte como backend apuntando a `http://localhost:8000/v1` con la
  `BYTE_API_KEY` como clave. El `model` se valida contra una lista blanca: un
  nombre arbitrario nunca llega a Ollama

  ```python
  from openai import OpenAI

  c = OpenAI(base_url="http://localhost:8000/v1", api_key="<BYTE_API_KEY>")
  c.chat.completions.create(model="byte", messages=[{"role": "user", "content": "hola"}])
  ```

  Lo que entra por ahí es el agente completo, con sus herramientas: una pregunta
  que necesite buscar en la web la busca. Las conversaciones quedan guardadas y
  se ven en la web y en `byte conversations`. El modo seguro es la excepción —
  no hay forma de pedir una aprobación humana en ese formato, así que un run que
  la necesite se corta y lo dice

- **n8n** (opcional, `n8n/`): tres workflows listos para importar — ingesta
  automática de documentos al RAG, un canal de email para preguntarle a Byte
  desde el correo, y n8n como servidor MCP para que Byte use sus herramientas.
  Va en un perfil aparte del compose (`docker compose --profile n8n up -d n8n`),
  así que no pesa si no se usa. Los JSON no llevan credenciales: cada nodo dice
  en sus notas cuál necesita

### Fase 2 — memoria y RAG

- **Documentos**: `POST /documents` sube PDF, TXT o Markdown (máx. 20 MB, tipo
  validado por magic bytes), responde `202` e indexa en segundo plano. El estado
  se sigue con `GET /documents/{id}`: `processing` → `indexed` | `error`
- **Búsqueda híbrida** sobre `pgvector`: similitud de vector (embeddings de
  `nomic-embed-text`, índice HNSW) combinada con coincidencia léxica
  (`tsvector` + GIN). Expuesta en `POST /search` y como herramienta `doc_search`
  del agente
- **Citas**: los fragmentos que usó el agente quedan en `MESSAGES.metadata` con
  su `document_id` y `chunk_id`. Los chunks vienen de archivos de terceros, así
  que entran al prompt marcados como contenido no confiable, y leer un documento
  y querer ejecutar código en el mismo run dispara el modo seguro
- **Compactación**: cuando el historial pasa el ~60% del contexto, los mensajes
  viejos se resumen en `CONVERSATIONS.summary` en vez de descartarse, y el
  resumen se inyecta en cada turno. Los originales no se borran: la UI los sigue
  mostrando. `POST /conversations/{id}/compact` la fuerza a mano

### Fase 1 — ejecución de código

- **Servicio `sandbox/`** (Node + Pyodide): corre el código que escribe el
  agente dentro de WebAssembly, con un intérprete nuevo por ejecución. Cada capa
  de aislamiento se verificó contra un Pyodide sin endurecer, donde el vector
  **funcionaba** — el detalle y la tabla completa están en
  [`sandbox/README.md`](sandbox/README.md)
- **`POST /execute`** para ejecutar directo (lo que va a usar `byte run`), y
  `code_exec` como segunda herramienta del agente
- **Modo seguro (HITL)**: si en la conversación entró contenido externo —una
  búsqueda web o un documento del RAG— y el agente quiere ejecutar código, el run
  se **detiene** y espera confirmación humana, aunque nadie lo haya pedido. La
  combinación "contenido de terceros + ejecutar código" es justo la que permite
  que una inyección indirecta llegue a correr algo. Cuenta la conversación
  entera, no el run: si mirara solo el run, partir el ataque en dos mensajes lo
  evadiría. Se modela como estado AG-UI (`awaiting_approval`) y se retoma con
  `POST /runs/{id}/resume`, en el mismo run, desde el checkpoint
- **`evals/`** con 10 tareas para detectar regresiones del agente

### Fase 0 — el MVP

Implementado:
- **API** según el contrato: `/health`, `/health/details`, conversaciones (CRUD con
  paginación por cursor), `POST /conversations/{id}/messages` → `GET /runs/{id}/events`,
  `POST /runs/{id}/cancel`, `GET /runs/{id}`, `GET /messages/{id}`, `GET /tools`
- **Streaming SSE con eventos AG-UI** (no nombres propios): `RUN_STARTED`, `STEP_*`,
  `TOOL_CALL_*`, `TEXT_MESSAGE_*`, `STATE_SNAPSHOT`/`STATE_DELTA`, `RUN_FINISHED`,
  `RUN_ERROR`; cada evento con `id:` para reconectar con `Last-Event-ID`
- **Agente LangGraph** con `StateGraph` propio: `retrieve_context` → `agent` →
  `should_continue` → `tools` → `finalize`, checkpointer con `thread_id = conversation_id`
- **Búsqueda web (Tavily)** como única herramienta, con argumentos validados por Pydantic
- **Logging estructurado** con `structlog` y `request_id` en logs y errores
- **Seguridad de Fase 0**: API key hasheada comparada en tiempo constante, cookie
  `httpOnly` + `SameSite=Strict` para la web, `events_token` firmado de 60 s de un solo
  uso ligado al run, rate limiting por credencial, tope de runs concurrentes, timeout y
  tope de iteraciones por run, resultados de herramientas delimitados como datos no
  confiables, CSP/HSTS/nosniff, CORS restringido, `gitleaks` y `pip-audit` en CI
- **Un run por conversación** (`409`): dos a la vez compartirían el hilo del
  checkpointer y se pisarían el estado. Borrar una conversación corta sus runs en vuelo
- **`BYTE_ENV=prod` no arranca sin Postgres**: el checkpointer nunca queda en memoria
  en producción, como pide el plan
- **Página HTML mínima** que consume el SSE (sin diseño: la identidad Byte llega en la Fase 5)
- **158 tests de Python + 25 del sandbox**, con dobles de Ollama, Tavily y el
  sandbox. Los del repositorio y el checkpointer corren contra las dos
  implementaciones: en memoria siempre, y contra Postgres cuando hay uno
  (se saltean si no)

Pendiente de Fase 0:
- **Correr el agente contra un Ollama real.** El código y los tests están, pero los
  tests usan un modelo falso: todavía no se ejecutó una conversación contra Ollama.
  Primer paso: `qwen2.5-coder:7b`, medir RAM con `num_ctx` explícito; después el
  30B-A3B
- Deploy de prueba en Railway con la RAM mínima y medir el costo real
- Límite de gasto y alertas en Railway (se configura en el dashboard)

Decisiones que se corrieron de fase, a propósito:
- `POST /runs/{id}/resume` y el modo seguro (HITL) van con el sandbox (Fase 1): hoy
  `safe_mode` se acepta y se reporta, pero no hay herramienta peligrosa que aprobar
- `Idempotency-Key` queda para la Fase 4, junto con el resto del endurecimiento de la API
- Markdown sanitizado con `nh3`: la página mínima pinta con `textContent`, así que
  no hay HTML que sanear hasta la Fase 5

## Licencia
MIT
