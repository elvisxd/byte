"""Corre el set de evals contra una instancia de Byte.

Necesita la API levantada y, según la tarea, Ollama, Tavily y el sandbox. No
corre en CI: el resultado depende del modelo, y la idea es compararlo entre
cambios propios, no tener un semáforo verde.

    uv run python -m evals.correr --url http://localhost:8000 --api-key $BYTE_API_KEY
    uv run python -m evals.correr --solo codigo   # una categoría
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from evals.evaluar import Resultado, evaluar

TAREAS = Path(__file__).parent / "tareas.json"


async def correr_tarea(
    client: httpx.AsyncClient, api_key: str, tarea: dict[str, Any]
) -> tuple[Resultado, float]:
    """Una conversación nueva por tarea: sin contaminación entre evals."""
    headers = {"X-API-Key": api_key}
    arranque = time.perf_counter()

    conversacion = await client.post("/api/v1/conversations", json={}, headers=headers)
    conversacion.raise_for_status()
    conversation_id = conversacion.json()["id"]

    respuesta = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        params={"wait": "true"},
        json={"content": tarea["prompt"]},
        headers=headers,
    )
    elapsed = time.perf_counter() - arranque

    if respuesta.status_code == 202:
        # El run quedó esperando aprobación (modo seguro). Para el eval alcanza
        # con registrar la pausa: no se aprueba nada automáticamente.
        cuerpo = respuesta.json()
        return Resultado(status=cuerpo.get("status", "paused")), elapsed

    if respuesta.status_code != 200:
        detalle = respuesta.json().get("error", {}).get("code", respuesta.status_code)
        return Resultado(content="", status=f"error:{detalle}"), elapsed

    cuerpo = respuesta.json()
    mensaje = cuerpo["message"]
    metadata = mensaje.get("metadata") or {}
    return (
        Resultado(
            content=mensaje.get("content", ""),
            tools_used=tuple(metadata.get("tools_used") or ()),
            iterations=int(metadata.get("iterations") or 0),
            status="finished",
        ),
        elapsed,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Corre los evals de Byte")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--solo", help="correr solo una categoría o un id")
    args = parser.parse_args()

    datos = json.loads(TAREAS.read_text(encoding="utf-8"))
    tareas = [t for t in datos["tareas"] if not args.solo or args.solo in (t["categoria"], t["id"])]
    if not tareas:
        print(f"No hay tareas que coincidan con {args.solo!r}")
        return 2

    pasaron = 0
    async with httpx.AsyncClient(base_url=args.url, timeout=300) as client:
        for tarea in tareas:
            try:
                resultado, elapsed = await correr_tarea(client, args.api_key, tarea)
            except httpx.HTTPError as exc:
                print(f"✗ {tarea['id']}: no se pudo consultar la API ({type(exc).__name__})")
                continue

            fallas = evaluar(tarea["espera"], resultado)
            if fallas:
                print(f"✗ {tarea['id']} ({elapsed:.1f}s)")
                for falla in fallas:
                    print(f"    {falla}")
                if resultado.content:
                    print(f"    respuesta: {resultado.content[:160]!r}")
            else:
                pasaron += 1
                herramientas = ", ".join(resultado.tools_used) or "sin herramientas"
                print(f"✓ {tarea['id']} ({elapsed:.1f}s, {herramientas})")

    print(f"\n{pasaron}/{len(tareas)} tareas pasaron")
    return 0 if pasaron == len(tareas) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
