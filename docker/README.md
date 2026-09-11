# docker/

Dockerfiles y compose de cada servicio.

- `docker-compose.yml` — entorno local: Postgres con `pgvector` y Ollama. La API
  se puede levantar con el perfil `full` (`docker compose --profile full up`),
  pero en desarrollo conviene correrla fuera del contenedor para tener recarga.
- `Dockerfile.api` — imagen de la API (FastAPI + agente). Usuario sin root,
  dependencias desde `uv.lock`.

Los puertos se publican solo en `127.0.0.1`: Ollama no tiene autenticación y su
API permite descargar o borrar modelos, así que nunca debe quedar expuesto.

Pendiente: `Dockerfile.sandbox` (Fase 1, Pyodide) y el deploy en Railway
(Fase 7), donde cada servicio va separado por red privada.
