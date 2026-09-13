"""Ejecución de código: cliente del servicio sandbox (Pyodide/WASM).

La API nunca ejecuta el código del modelo en su propio proceso — nada de
`eval`/`exec`. Todo va al servicio `sandbox/`, que corre aislado, sin red y sin
secretos (ver `sandbox/README.md` y `docs/seguridad-byte.md`).
"""

from typing import Any

import httpx
from pydantic import BaseModel, Field

from api.logging import get_logger
from tools.base import Tool, ToolResult, wrap_untrusted

logger = get_logger("tools.code_exec")

# Tope de la herramienta, más bajo que el del endpoint /execute: lo que escribe
# el modelo en un turno no necesita 50 KB, y acota lo que entra al prompt.
MAX_CODE_CHARS = 10_000
TIMEOUT_RED_EXTRA_S = 5.0


class CodeExecArgs(BaseModel):
    """Argumentos válidos. Lo que no encaje acá, no se ejecuta."""

    code: str = Field(description="Código Python a ejecutar. Imprimí lo que quieras ver.")
    timeout_s: int = Field(default=10, ge=1, le=30, description="Segundos máximos de ejecución")


class SandboxNoDisponible(RuntimeError):
    """El servicio sandbox no respondió o respondió un error."""


async def ejecutar_en_sandbox(
    sandbox_url: str,
    internal_token: str,
    code: str,
    timeout_s: int,
    client: Any | None = None,
) -> dict[str, Any]:
    """Llama al servicio sandbox. Lo usan la herramienta y el endpoint /execute."""
    payload = {"language": "python", "code": code, "timeout_s": timeout_s}
    headers = {"X-Internal-Token": internal_token}
    url = f"{sandbox_url.rstrip('/')}/execute"
    # El sandbox ya corta por timeout; el cliente espera un poco más para
    # distinguir "el código tardó" de "el servicio no responde".
    timeout = timeout_s + TIMEOUT_RED_EXTRA_S
    try:
        if client is not None:
            respuesta = await client.post(url, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=timeout) as http:
                respuesta = await http.post(url, json=payload, headers=headers)
        respuesta.raise_for_status()
        return respuesta.json()
    except Exception as exc:
        logger.warning("sandbox_no_responde", error_type=type(exc).__name__)
        raise SandboxNoDisponible(str(exc)) from exc


async def sandbox_status(sandbox_url: str, timeout_s: float = 2.0) -> str:
    """ "ok" si el sandbox responde; "caido" si no. Alimenta /health/details."""
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as http:
            respuesta = await http.get(f"{sandbox_url.rstrip('/')}/health")
            respuesta.raise_for_status()
            return "ok" if respuesta.json().get("status") == "ok" else "degradado"
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("sandbox_health_fallo", error_type=type(exc).__name__)
        return "caido"


def _aviso_de_modulo(stderr: str) -> str:
    """Aclara los `ModuleNotFoundError`, que de otro modo hacen insistir al modelo.

    Pyodide dice "el módulo está incluido en la distribución pero no instalado",
    que suena a que se puede instalar. En este sandbox no se puede: no hay
    `micropip` ni red, a propósito. Sin esta aclaración el modelo gasta todas sus
    vueltas intentando un `pip install` que nunca va a funcionar — medido: cinco
    intentos seguidos con numpy hasta agotar el límite de iteraciones.
    """
    if "ModuleNotFoundError" not in stderr:
        return ""
    return (
        "Este sandbox trae solo la biblioteca estándar de Python: no hay pip ni "
        "micropip ni red, así que ese módulo no se puede instalar. Resolvelo con "
        "la stdlib (`statistics`, `math`, `json`, `itertools`…) o explicá por qué "
        "no se puede."
    )


def _formatear(resultado: dict[str, Any]) -> str:
    """Arma la salida que ve el modelo, con las partes separadas."""
    partes = []
    if resultado.get("stdout"):
        partes.append(f"stdout:\n{resultado['stdout'].rstrip()}")
    if resultado.get("stderr"):
        partes.append(f"stderr:\n{resultado['stderr'].rstrip()}")
        partes.append(_aviso_de_modulo(resultado["stderr"]))
    partes = [p for p in partes if p]
    if not partes:
        partes.append("(sin salida: acordate de usar print())")
    partes.append(f"exit_code: {resultado.get('exit_code')}")
    if resultado.get("truncated"):
        partes.append("(salida recortada por tamaño)")
    return "\n\n".join(partes)


def build_code_exec_tool(
    sandbox_url: str,
    internal_token: str,
    max_result_chars: int,
    client: Any | None = None,
) -> Tool:
    """Arma la herramienta. `client` se inyecta en los tests."""

    async def run(args: BaseModel) -> ToolResult:
        assert isinstance(args, CodeExecArgs)  # noqa: S101 - lo garantiza el nodo de tools
        if len(args.code) > MAX_CODE_CHARS:
            return ToolResult(
                content=wrap_untrusted(
                    "EJECUCION DE CODIGO",
                    f"El código supera los {MAX_CODE_CHARS} caracteres. Partilo en pedazos.",
                    max_result_chars,
                ),
                summary={"ok": False, "error": "codigo_demasiado_largo"},
                ok=False,
            )

        try:
            resultado = await ejecutar_en_sandbox(
                sandbox_url, internal_token, args.code, args.timeout_s, client=client
            )
        except SandboxNoDisponible:
            # El error vuelve al modelo como dato, no revienta el grafo.
            return ToolResult(
                content=wrap_untrusted(
                    "EJECUCION DE CODIGO",
                    "El sandbox no respondió. Respondé con lo que sepas sin ejecutar código.",
                    max_result_chars,
                ),
                summary={"ok": False, "error": "sandbox_no_disponible"},
                ok=False,
            )

        exit_code = resultado.get("exit_code", 1)
        return ToolResult(
            content=wrap_untrusted("EJECUCION DE CODIGO", _formatear(resultado), max_result_chars),
            summary={
                "ok": exit_code == 0,
                "exit_code": exit_code,
                "duration_ms": resultado.get("duration_ms"),
                "truncated": bool(resultado.get("truncated")),
            },
            ok=exit_code == 0,
        )

    return Tool(
        name="code_exec",
        description=(
            "Ejecuta código Python en un sandbox aislado y devuelve su salida. "
            "Usalo para calcular, probar código o verificar resultados. Solo hay "
            "biblioteca estándar: no hay internet ni paquetes externos. Imprimí "
            "con print() lo que quieras ver."
        ),
        args_model=CodeExecArgs,
        run=run,
        source="builtin",
    )
