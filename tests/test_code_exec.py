"""La herramienta de ejecución de código y el endpoint /execute.

No levantan el sandbox real: usan un doble. El sandbox de verdad tiene su propia
suite en `sandbox/test/` (25 tests, incluidos los de escape).
"""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import AUTH
from tools.code_exec import (
    MAX_CODE_CHARS,
    CodeExecArgs,
    SandboxNoDisponible,
    build_code_exec_tool,
    ejecutar_en_sandbox,
    sandbox_status,
)


class RespuestaFalsa:
    def __init__(self, datos: dict[str, Any]) -> None:
        self._datos = datos

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._datos


class SandboxFalso:
    """Doble del servicio sandbox."""

    def __init__(self, resultado: dict[str, Any] | None = None) -> None:
        self.resultado = resultado or {
            "stdout": "42\n",
            "stderr": "",
            "exit_code": 0,
            "duration_ms": 7,
            "truncated": False,
        }
        self.pedidos: list[dict[str, Any]] = []

    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> RespuestaFalsa:
        self.pedidos.append({"url": url, "json": json, "headers": headers})
        return RespuestaFalsa(self.resultado)


class SandboxRoto:
    async def post(self, *_args: Any, **_kwargs: Any) -> RespuestaFalsa:
        raise ConnectionError("sin ruta al host")


async def test_ejecuta_y_resume_para_la_ui() -> None:
    sandbox = SandboxFalso()
    tool = build_code_exec_tool("http://sandbox:3000", "tok", 4000, client=sandbox)
    resultado = await tool.run(CodeExecArgs(code="print(6*7)"))

    assert resultado.ok is True
    assert resultado.summary == {
        "ok": True,
        "exit_code": 0,
        "duration_ms": 7,
        "truncated": False,
    }
    # La salida del sandbox entra al prompt como dato, no como instrucción.
    assert "CONTENIDO EXTERNO NO CONFIABLE" in resultado.content
    assert "42" in resultado.content
    # El token interno viaja en el header, nunca en el cuerpo ni en la URL.
    assert sandbox.pedidos[0]["headers"] == {"X-Internal-Token": "tok"}
    assert sandbox.pedidos[0]["json"]["language"] == "python"


async def test_el_error_de_python_vuelve_como_dato() -> None:
    sandbox = SandboxFalso(
        {
            "stdout": "",
            "stderr": "NameError: name 'x' is not defined",
            "exit_code": 1,
            "duration_ms": 3,
            "truncated": False,
        }
    )
    tool = build_code_exec_tool("http://sandbox:3000", "tok", 4000, client=sandbox)
    resultado = await tool.run(CodeExecArgs(code="print(x)"))

    assert resultado.ok is False
    assert resultado.summary["exit_code"] == 1
    # El modelo necesita ver el error para corregirse.
    assert "NameError" in resultado.content


async def test_sandbox_caido_no_rompe_el_run() -> None:
    tool = build_code_exec_tool("http://sandbox:3000", "tok", 4000, client=SandboxRoto())
    resultado = await tool.run(CodeExecArgs(code="print(1)"))
    assert resultado.ok is False
    assert resultado.summary["error"] == "sandbox_no_disponible"
    assert "no respondió" in resultado.content


async def test_codigo_demasiado_largo_no_llega_al_sandbox() -> None:
    sandbox = SandboxFalso()
    tool = build_code_exec_tool("http://sandbox:3000", "tok", 4000, client=sandbox)
    resultado = await tool.run(CodeExecArgs(code="x = 1\n" * MAX_CODE_CHARS))
    assert resultado.ok is False
    assert resultado.summary["error"] == "codigo_demasiado_largo"
    assert sandbox.pedidos == []


async def test_timeout_fuera_de_rango_no_valida() -> None:
    with pytest.raises(ValueError, match="less than or equal to 30"):
        CodeExecArgs(code="print(1)", timeout_s=120)


async def test_helper_propaga_sandbox_no_disponible() -> None:
    with pytest.raises(SandboxNoDisponible):
        await ejecutar_en_sandbox("http://sandbox:3000", "tok", "print(1)", 5, client=SandboxRoto())


async def test_sandbox_status_sin_servicio() -> None:
    # Sin nada escuchando, el estado es "caido" y no una excepción.
    assert await sandbox_status("http://127.0.0.1:1", timeout_s=0.3) == "caido"


# --- Endpoint /execute ---


def test_execute_sin_sandbox_configurado(cliente: TestClient) -> None:
    respuesta = cliente.post("/api/v1/execute", json={"code": "print(1)"}, headers=AUTH)
    assert respuesta.status_code == 503
    assert respuesta.json()["error"]["code"] == "sandbox_not_configured"


def test_execute_pide_credencial(cliente: TestClient) -> None:
    assert cliente.post("/api/v1/execute", json={"code": "print(1)"}).status_code == 401


def test_execute_valida_el_lenguaje(cliente: TestClient) -> None:
    respuesta = cliente.post(
        "/api/v1/execute", json={"language": "javascript", "code": "1"}, headers=AUTH
    )
    assert respuesta.status_code == 422


def test_execute_valida_el_timeout(cliente: TestClient) -> None:
    respuesta = cliente.post(
        "/api/v1/execute", json={"code": "print(1)", "timeout_s": 999}, headers=AUTH
    )
    assert respuesta.status_code == 422


def test_la_herramienta_se_registra_con_el_sandbox_configurado(
    crear_cliente: Callable[..., TestClient],
) -> None:
    cliente = crear_cliente(
        con_busqueda=False,
        con_sandbox=True,
        SANDBOX_URL="http://sandbox:3000",
        SANDBOX_TOKEN="tok",
    )
    assert cliente.get("/api/v1/tools", headers=AUTH).json() == {
        "tools": [{"name": "code_exec", "source": "builtin"}]
    }


def test_un_modulo_que_falta_aclara_que_no_se_puede_instalar() -> None:
    """Pyodide dice "el módulo está incluido en la distribución pero no
    instalado", que suena a que se puede instalar. En este sandbox no se puede
    —no hay micropip ni red, a propósito— y sin la aclaración el modelo gasta
    todas sus vueltas intentando un `pip install` que nunca va a funcionar.

    Medido contra granite4.1: cinco intentos hasta agotar el límite de
    iteraciones, contra dos y una respuesta correcta con el aviso puesto.
    """
    from tools.code_exec import _formatear

    salida = _formatear({"stderr": "ModuleNotFoundError: No module named 'numpy'", "exit_code": 1})
    assert "no se puede instalar" in salida
    assert "stdlib" in salida or "estándar" in salida


def test_un_error_normal_no_lleva_el_aviso() -> None:
    """El aviso sirve para un módulo que falta; en un ZeroDivisionError sería
    ruido que empuja al modelo hacia una pista equivocada."""
    from tools.code_exec import _formatear

    salida = _formatear({"stderr": "ZeroDivisionError: division by zero", "exit_code": 1})
    assert "no se puede instalar" not in salida
