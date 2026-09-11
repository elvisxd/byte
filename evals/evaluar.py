"""Chequeo de una tarea de eval contra el resultado del agente.

Está separado del runner para poder testearlo sin levantar nada.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Resultado:
    """Lo que devolvió el agente para una tarea."""

    content: str = ""
    tools_used: tuple[str, ...] = ()
    iterations: int = 0
    status: str = "finished"


def evaluar(espera: dict[str, Any], resultado: Resultado) -> list[str]:
    """Devuelve la lista de fallas. Vacía significa que la tarea pasó."""
    fallas: list[str] = []
    texto = resultado.content.lower()

    for esperado in espera.get("contiene", []):
        if esperado.lower() not in texto:
            fallas.append(f"falta {esperado!r} en la respuesta")

    alguno = espera.get("contiene_alguno", [])
    if alguno and not any(opcion.lower() in texto for opcion in alguno):
        fallas.append(f"no aparece ninguno de {alguno}")

    for prohibido in espera.get("no_contiene", []):
        if prohibido.lower() in texto:
            fallas.append(f"aparece {prohibido!r}, que no debería")

    usadas = set(resultado.tools_used)
    for herramienta in espera.get("herramientas", []):
        if herramienta not in usadas:
            fallas.append(f"no usó {herramienta} (usó: {sorted(usadas) or 'ninguna'})")

    if espera.get("sin_herramientas") and usadas:
        fallas.append(f"usó herramientas de más: {sorted(usadas)}")

    estado_esperado = espera.get("estado")
    if estado_esperado and resultado.status != estado_esperado:
        fallas.append(f"estado {resultado.status!r}, se esperaba {estado_esperado!r}")
    if not estado_esperado and resultado.status not in ("finished", ""):
        fallas.append(f"el run terminó en {resultado.status!r}")

    tope = espera.get("max_iteraciones")
    if tope is not None and resultado.iterations > tope:
        fallas.append(f"{resultado.iterations} iteraciones, el tope era {tope}")

    return fallas
