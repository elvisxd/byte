# api/
Rutas FastAPI, esquemas de entrada/salida y streaming SSE (eventos AG-UI).
Ver `docs/api-contrato-byte.md`.

- `main.py` — fábrica de la app: middlewares, cabeceras de seguridad, arranque
- `config.py` — configuración por entorno (`.env`) y límites
- `deps.py` — contexto de la app, autenticación y rate limiting
- `security.py` — API key hasheada, cookie de sesión, `events_token`, CSP
- `errors.py` — formato de error del contrato con `request_id`
- `logging.py` — `structlog` (sin prompts en INFO)
- `routes/` — salud, sesión, conversaciones, runs y herramientas
