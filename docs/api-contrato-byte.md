# Contrato de la API — Byte (FastAPI) — v2 revisado

Prefijo: `/api/v1`. Respuestas en JSON salvo el streaming (SSE). FastAPI genera el OpenAPI en `/docs`; el CLI en Go genera su cliente tipado desde ese spec con `oapi-codegen`.

## Autenticación y protección
- **CLI y scripts:** header `X-API-Key: <clave>` (MVP) → `Authorization: Bearer <jwt>` (Fase 4). Los endpoints no cambian
- **Web:** sesión por **cookie `httpOnly` + `SameSite=Strict`**, porque `EventSource` no puede mandar headers y la API key nunca debe ir en la URL. Alternativa para `GET /runs/{id}/events`: token firmado de 60 s, un solo uso, ligado al `run_id`, obtenido en la respuesta del `POST …/messages` (`events_token`)
- La API key se compara en tiempo constante y se guarda hasheada
- Máximo 1-2 runs concurrentes por credencial (`429` si se excede)
- Rate limiting por credencial: general 60/min; `/runs` (crear) 10/min; `/execute` 10/min. Respuesta `429` con `Retry-After`
- Header opcional `Idempotency-Key` en `POST …/messages` y `POST /execute`: un reintento con la misma clave devuelve el mismo resultado sin volver a ejecutar
- Uploads validados por contenido (magic bytes), no por extensión

## Formato de errores
```json
{ "error": { "code": "model_unavailable", "message": "Ollama no responde", "request_id": "req_8f3a" } }
```
`400` body inválido · `401` sin credencial · `403` sin permiso · `404` no existe · `409` conflicto · `413` archivo o código demasiado grande · `422` validación · `429` rate limit · `500` interno · `503` modelo o servicio caído

---

## Salud
**GET `/health`** — público, mínimo: `{ "status": "ok" }` (o `503`)
**GET `/health/details`** — con API key: `{ "status", "model", "ollama", "db", "sandbox", "version" }`. Alimenta el indicador "En línea, corriendo local".

---

## Conversaciones
**POST `/conversations`** `{ "title"? }` → `201 Conversation`. Si no hay título, se genera truncando el primer mensaje (sin llamar al LLM).
**GET `/conversations?limit=20&cursor=`** → `{ "items": [ { "id", "title", "preview", "updated_at" } ], "next_cursor" }` ordenado por `updated_at` desc.
**GET `/conversations/{id}?limit=50&before=<message_id>&include_tool_messages=false`** → `{ ...Conversation, "summary", "summary_up_to_message_id", "messages": [ Message ], "has_more" }`. La UI usa `summary_up_to_message_id` para mostrar el marcador "conversación compactada hasta acá".
**PATCH `/conversations/{id}`** `{ "title" }` → `200`
**POST `/conversations/{id}/compact`** → `202 { "run_id" }`. Fuerza la compactación: el modelo resume los mensajes viejos y los guarda en `summary`. Normalmente se dispara solo cuando el historial supera ~60% del contexto del modelo. Los mensajes originales no se borran.
**DELETE `/conversations/{id}`** → `204`. Cancela los runs en vuelo de la conversación y borra mensajes **y el hilo del checkpointer de LangGraph** (`thread_id = id`).

---

## Mensajes y runs (el corazón del agente)
Un *run* es una ejecución del agente. Se crea con un mensaje y se observa por SSE. Separarlo en dos pasos es obligatorio: `EventSource` (y la extensión SSE de HTMX) solo soportan GET.

**POST `/conversations/{id}/messages`**
Body: `{ "content": "Buscá la última versión de FastAPI y armá el endpoint base", "safe_mode": false }`
→ `202 { "run_id", "message_id", "events_url": "/api/v1/runs/{run_id}/events", "events_token": "<firmado, 60 s, un solo uso>" }`
Con `?wait=true` espera y devuelve `{ "message": Message, "sources": [...] }` en un solo JSON (tests y scripts).
Si el run queda esperando aprobación (modo seguro), `?wait=true` responde `202 { "run_id", "status": "paused", "awaiting_approval": { code, reason, resume_token } }`: no hay mensaje final que devolver todavía, y el token viene ahí para poder continuar sin leer el SSE.
**`409 approval_pending`** si la conversación tiene un run esperando aprobación (modo seguro): hay que resolverlo con `/resume` antes de seguir, para no abandonar la decisión ni dejar el hilo del agente con un pedido de herramienta sin responder. Si el run pausado ya no existe (por ejemplo, se reinició el servicio), la API lo cierra sola como rechazo y deja seguir.
**`409 conversation_busy`** si la conversación ya tiene un run en curso: dos runs a la vez comparten el hilo del checkpointer (`thread_id = conversation_id`), se pisan el estado y cada uno responde sin ver la pregunta del otro. El tope de runs concurrentes por credencial sigue aplicando entre conversaciones distintas.

**GET `/runs/{run_id}/events`** — `text/event-stream`. Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`, sin gzip. Cada evento lleva `id:` para que el cliente reconecte con `Last-Event-ID` y retome donde quedó.

Los ids son **crecientes, no necesariamente consecutivos**: el texto se emite token a token y guardar todos los eventos de todos los runs no escala, así que los runs terminados más viejos sueltan sus deltas de texto. Cuando eso pasa, el stream arranca con `STATE_DELTA { replay_incompleto: true }` y el cliente pide el texto definitivo con `GET /messages/{id}` (el `message_id` viene en `RUN_FINISHED`). Un run en curso nunca pierde eventos.

Eventos: **protocolo AG-UI** (en vez de nombres propios). Los que usa Byte:

| Evento AG-UI | Cuándo | Para qué en la UI/CLI |
|---|---|---|
| `RUN_STARTED` | arranca el run | mostrar "escribiendo" |
| `STEP_STARTED` / `STEP_FINISHED` | entra/sale de un nodo del grafo (`retrieve_context`, `agent`, `tools`) | líneas de estado "Buscando en la web…", "Ejecutando código…" |
| `TOOL_CALL_START` / `TOOL_CALL_ARGS` / `TOOL_CALL_END` | el agente usa una herramienta | mostrar qué herramienta y con qué input |
| `TOOL_CALL_RESULT` | resultado resumido (el completo va a Langfuse) | "5 resultados", `ok: true/false` |
| `TEXT_MESSAGE_START` / `TEXT_MESSAGE_CONTENT` / `TEXT_MESSAGE_END` | texto de la respuesta, token a token | render del mensaje |
| `STATE_SNAPSHOT` / `STATE_DELTA` | estado compartido: `iteration_count`, `sources`, `awaiting_approval` | fuentes citadas, modo seguro |
| `RUN_FINISHED` | fin OK; payload incluye `message_id`, `langfuse_trace_id`, `sources` | cerrar |
| `RUN_ERROR` | error a mitad del run | mostrar error |

**Modo seguro (HITL):** AG-UI no tiene un evento especial de aprobación; se modela como estado. El run emite `STATE_DELTA { awaiting_approval: { code, resume_token } }` y termina con `RUN_FINISHED { status: "paused" }`. El cliente muestra el código y llama a:
**POST `/runs/{run_id}/resume`** `{ "resume_token", "approve": true|false }` → `202 { "run_id" }` (mismo run, continúa desde el checkpoint). `resume_token`: aleatorio, firmado, un solo uso, ligado a run y usuario. El modo seguro se activa **automáticamente** si en el run hubo búsqueda web y el agente quiere ejecutar código, aunque `safe_mode` sea `false`. El cliente se vuelve a suscribir a `/runs/{run_id}/events`.

**POST `/runs/{run_id}/cancel`** → `202`. Corta la generación en Ollama y el loop del grafo. Es el botón "detener" y evita quemar CPU por respuestas abandonadas.
**GET `/runs/{run_id}`** → `{ "status": "running" | "paused" | "finished" | "cancelled" | "error", "iterations", "started_at", "finished_at" }`

**GET `/messages/{id}`** → `Message`

> **Dos ids distintos, a propósito.** El `messageId` de los eventos `TEXT_MESSAGE_*` es el id del mensaje *dentro del run* (así funciona AG-UI: el cliente lo usa para ir armando la burbuja mientras llega el texto). El `message_id` de `RUN_FINISHED` es el id del registro ya guardado en `MESSAGES`, que es el que sirve para `GET /messages/{id}`.

### Esquema `Message`
```json
{
  "id": "uuid", "conversation_id": "uuid",
  "role": "user" | "assistant" | "tool",
  "content": "texto",
  "metadata": { "sources": [ { "document_id", "chunk_id", "filename", "snippet" } ], "tools_used": ["web_search"], "iterations": 2 },
  "langfuse_trace_id": "tr_...", "created_at": "2026-09-11T14:02:00Z"
}
```

---

## Documentos (RAG) — Fase 2
**POST `/documents`** — `multipart/form-data` con `file` (PDF, TXT, MD; máx. 20 MB; tipo validado por contenido). → `202 { "id", "filename", "status": "processing", "size_bytes", "mime_type", "uploaded_at" }`
**GET `/documents`** → `{ "items": [ { "id", "filename", "status": "processing" | "indexed" | "error", "progress", "chunks", "size_bytes", "uploaded_at" } ] }`
**GET `/documents/{id}`** → detalle con `progress` y `error_message`
**POST `/documents/{id}/reindex`** → `202`
**DELETE `/documents/{id}`** → `204` (borra sus chunks)
**POST `/search`** `{ "query", "top_k": 5 }` → `{ "results": [ { "chunk_id", "document_id", "filename", "snippet", "score" } ] }` — búsqueda híbrida directa

---

## Herramientas y ejecución
**GET `/tools`** → `{ "tools": [ { "name": "web_search", "source": "builtin" }, { "name": "n8n_ingest", "source": "mcp:n8n" } ] }`
**POST `/execute`** `{ "language": "python", "code": "...", "timeout_s": 10 }` → `{ "stdout", "stderr", "exit_code", "duration_ms", "truncated" }`
Límites: código máx. 50 KB (`413`), timeout máx. 30 s, salida máx. 64 KB.
`exit_code`: `0` si corrió, `1` si Python tiró una excepción (el traceback va en `stderr`), `124` si se cortó por timeout, `137` si se pasó de memoria. `503 sandbox_no_configurado` si faltan `SANDBOX_URL`/`SANDBOX_TOKEN`; `503 sandbox_unavailable` si el servicio no responde. El detalle de cómo está aislado está en `sandbox/README.md`.

---

## Compatibilidad OpenAI (opcional, muy recomendada — Fase 3)
**POST `/v1/chat/completions`** con el formato de OpenAI (incl. `stream: true`). El campo `model` se valida contra una **lista blanca**; nunca se pasa un nombre arbitrario a Ollama. Permite que clientes existentes — Open WebUI, Continue.dev en VS Code, cualquier SDK de OpenAI — usen a Byte como backend sin escribir nada. Costo bajo, impacto alto en portafolio.

## Capa GraphQL de solo lectura (opcional — Fase 5)
Si se quiere GraphQL en el CV: **Strawberry** montado en `/graphql` con *queries* sobre conversaciones, mensajes, documentos y búsqueda (con dataloaders para evitar N+1). Sin mutaciones ni subscriptions: los comandos y el streaming siguen en REST + SSE/AG-UI, que es lo que habla el ecosistema de IA. Patrón híbrido justificable en entrevista: GraphQL para agregación de datos, REST/SSE para comandos y streaming.

---

## Autenticación — Fase 4
**POST `/auth/register`** `{ email, password }` → `201` · **POST `/auth/login`** → `{ "access_token", "token_type": "bearer", "expires_in" }` · **GET `/me`**

---

## Mapeo con el CLI en Go (cliente generado con `oapi-codegen`)
| Comando | Endpoints |
|---|---|
| `byte ask "..."` | `POST /conversations/{id}/messages` → `GET /runs/{id}/events` (SSE) |
| `byte search "..."` | igual que `ask`, o `POST /search` para buscar solo en documentos |
| `byte run script.py` | `POST /execute` |
| `byte docs add archivo.pdf` | `POST /documents` |
| `byte stop` | `POST /runs/{id}/cancel` |
| `byte status` | `GET /health/details` |

## Seguridad
Ver `seguridad-byte.md`. Resumen aplicado a la API: servicios internos (Ollama, Postgres, sandbox) sin dominio público; cookie httpOnly para la web; resultados de herramientas delimitados y acotados en el prompt; argumentos de herramientas validados con Pydantic; Markdown sanitizado; cabeceras CSP/HSTS; CORS restringido; todo filtrado por `user_id` en multi-usuario.

## Estado de implementación (Fases 0 y 1)
Implementado: salud, conversaciones (CRUD + cursor), mensajes y runs (crear, SSE,
cancelar, consultar, **reanudar**), `GET /messages/{id}`, `GET /tools`,
**`POST /execute`**, y `POST /session` + `DELETE /session` para la cookie de la
web (agregados acá, no estaban en la v2). El spec de `/docs` ya declara los
modelos de respuesta y el envoltorio de error, así que sirve para generar el
cliente del CLI con `oapi-codegen`.

Pendiente, con su fase: `Idempotency-Key` (Fase 4), compatibilidad OpenAI
(Fase 3). `/compact`, documentos y `/search` ya están (Fase 2).

## Decisiones
- Runs como recurso propio: separa crear (POST) de observar (GET SSE), habilita cancelar, reconectar y consultar estado
- SSE (no WebSockets): unidireccional, simple, con reconexión nativa, compatible con HTMX, Go y AG-UI
- Eventos según **AG-UI**: estándar abierto, integración de primera clase con LangGraph, complementa MCP (herramientas) y A2A (agentes)
- Resultados completos de herramientas van a Langfuse; el cliente recibe resúmenes
- `202` en documentos: indexación asíncrona
- Paginación por cursor
- GraphQL solo como capa de lectura opcional; el núcleo queda en REST + SSE para no salirse del ecosistema de IA (y poder ser compatible con OpenAI)
