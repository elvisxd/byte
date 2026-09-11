# Byte — Agente de IA local (self-hosted)

> Un agente de IA propio que corre un modelo open source localmente, capaz de programar, buscar en internet y usar herramientas vía MCP — sin depender de APIs pagas.

**Estado:** fase de diseño cerrada · MVP (Fase 0) en progreso.

## Stack
Python · FastAPI · LangGraph · Ollama (Qwen3-Coder-30B-A3B) · PostgreSQL + pgvector · MCP · AG-UI · Railway · Go (CLI)

## Arquitectura en una línea
`web / CLI` → `FastAPI (runs + SSE con eventos AG-UI)` → `agente LangGraph (checkpointer en Postgres)` → herramientas: `búsqueda web (Tavily)`, `sandbox WASM (Pyodide)`, `RAG híbrido (pgvector)`, `MCP (n8n y otros)`.

## Documentación
- [Plan de acción y arquitectura](docs/plan-asistente-ia-local.md) — visión, stack, estructura, ERD, flujo del agente, fases, decisiones y bugs de diseño detectados
- [Contrato de la API](docs/api-contrato-byte.md) — endpoints, runs, eventos AG-UI, documentos, sandbox
- [Seguridad: modelo de amenazas y checklist](docs/seguridad-byte.md) — OWASP LLM 2025 + Agentic 2026
- [Identidad visual y prompts de Canva](docs/prompts-canva-byte.md)

## Estructura
Un módulo por carpeta; cada carpeta tiene su README explicando qué va ahí.

```
api/  agent/  tools/  sandbox/  mcp/  rag/  models/  db/  web/  cli/  n8n/  docker/  tests/  evals/  docs/
```

## Cómo correrlo
Pendiente hasta la Fase 0. Copiar `.env.example` a `.env` y completar.

## Licencia
MIT
