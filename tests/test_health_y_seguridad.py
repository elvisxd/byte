"""Salud, cabeceras de seguridad y formato de errores."""

import pytest
from fastapi.testclient import TestClient

from tests.conftest import API_KEY, AUTH


def test_health_es_publico(cliente: TestClient) -> None:
    respuesta = cliente.get("/api/v1/health")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"status": "ok"}


def test_health_details_pide_credencial(cliente: TestClient) -> None:
    assert cliente.get("/api/v1/health/details").status_code == 401


def test_health_details_reporta_el_estado(cliente: TestClient) -> None:
    respuesta = cliente.get("/api/v1/health/details", headers=AUTH)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["db"] == "ok"
    # Sin Ollama levantado, el estado general es degradado (no un 500).
    assert cuerpo["ollama"] == "caido"
    assert cuerpo["status"] == "degraded"
    assert cuerpo["sandbox"] == "no_configurado"


def test_cabeceras_de_seguridad(cliente: TestClient) -> None:
    cabeceras = cliente.get("/api/v1/health").headers
    assert "default-src 'self'" in cabeceras["content-security-policy"]
    assert "unsafe-inline" not in cabeceras["content-security-policy"]
    assert cabeceras["x-content-type-options"] == "nosniff"
    assert cabeceras["x-frame-options"] == "DENY"
    assert cabeceras["referrer-policy"] == "no-referrer"
    assert cabeceras["x-request-id"].startswith("req_")


def test_sin_hsts_en_dev(cliente: TestClient) -> None:
    # HSTS solo con HTTPS real: en dev rompería localhost.
    assert "strict-transport-security" not in cliente.get("/api/v1/health").headers


def test_formato_de_error(cliente: TestClient) -> None:
    respuesta = cliente.get("/api/v1/conversations")
    assert respuesta.status_code == 401
    error = respuesta.json()["error"]
    assert error["code"] == "unauthorized"
    assert error["request_id"].startswith("req_")
    assert "message" in error


def test_error_de_validacion(cliente: TestClient) -> None:
    respuesta = cliente.patch("/api/v1/conversations/x", json={"title": ""}, headers=AUTH)
    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["code"] == "validation_error"


def test_la_pagina_minima_se_sirve(cliente: TestClient) -> None:
    respuesta = cliente.get("/")
    assert respuesta.status_code == 200
    assert "Byte" in respuesta.text
    # El JS va aparte para que la CSP pueda prohibir inline.
    assert "/static/app.js" in respuesta.text


def test_csp_permite_swagger_solo_en_docs(cliente: TestClient) -> None:
    """La excepción del CDN aplica a /docs, no a la API ni a la página."""
    docs = cliente.get("/docs").headers["content-security-policy"]
    assert "https://cdn.jsdelivr.net" in docs

    for ruta in ("/", "/api/v1/health"):
        csp = cliente.get(ruta).headers["content-security-policy"]
        assert "jsdelivr" not in csp
        assert "unsafe-inline" not in csp


def test_el_env_del_desarrollador_no_se_cuela_en_los_tests() -> None:
    """`Settings` lee `env_file=".env"`, así que borrar una variable del entorno
    no alcanza: lo que esté en el archivo gana.

    Sin esto, tener un sandbox o una clave de Tavily configurados hace fallar
    justamente los tests que prueban que no están —y el fallo parece del código,
    no del entorno. Pasó: tres tests rojos durante toda una sesión.
    """
    from api.config import Settings

    ajustes = Settings()
    assert ajustes.sandbox_url == "", "el SANDBOX_URL del .env llegó al test"
    assert ajustes.tavily_api_key == "", "la TAVILY_API_KEY del .env llegó al test"
    assert ajustes.mcp_servers == "", "los BYTE_MCP_SERVERS del .env llegaron al test"
    # Los tests falsean el LLM: que Ollama esté o no corriendo no debería
    # cambiar el resultado de la suite.
    assert "11434" not in ajustes.ollama_base_url


def test_el_bearer_sirve_en_toda_la_api_pero_solo_con_la_clave(cliente: TestClient) -> None:
    """El header `Authorization: Bearer` se agregó para los clientes de OpenAI,
    pero vale en cualquier ruta: es la misma credencial por otro header.

    Lo que no puede pasar es que aceptar un header más ablande la puerta, así
    que se verifica también que un Bearer cualquiera siga siendo 401.
    """
    assert (
        cliente.get("/api/v1/tools", headers={"Authorization": "Bearer " + API_KEY}).status_code
        == 200
    )
    assert (
        cliente.get("/api/v1/tools", headers={"Authorization": "Bearer otra-cosa"}).status_code
        == 401
    )
    assert cliente.get("/api/v1/tools", headers={"Authorization": "Bearer "}).status_code == 401
    # Sin el prefijo no es un Bearer: mandar la clave pelada no debería alcanzar.
    assert cliente.get("/api/v1/tools", headers={"Authorization": API_KEY}).status_code == 401


def test_el_credential_id_no_revela_el_hash_de_la_clave() -> None:
    """Ese valor sale al cliente: va en la cookie de sesión y dentro del
    `resume_token`, que la API devuelve en un cuerpo JSON.

    Si fuera un prefijo del SHA-256 sin sal de la API key —como era— serviría de
    oráculo para confirmar los aciertos de un diccionario offline. Contra una
    clave de `openssl rand -hex 32` da igual; contra una elegida a mano, no.
    """
    import hashlib

    from api.security import Credentials

    clave = "cambiame"
    credenciales = Credentials(clave, "s" * 32)
    hash_de_la_clave = hashlib.sha256(clave.encode()).hexdigest()

    assert credenciales.credential_id not in hash_de_la_clave
    assert not hash_de_la_clave.startswith(credenciales.credential_id)


def test_el_credential_id_es_estable_entre_reinicios() -> None:
    """El rate limiting y el dueño de un run lo necesitan estable: si cambiara
    en cada arranque, los runs de antes quedarían sin dueño reconocible."""
    from api.security import Credentials

    primero = Credentials("la-clave", "s" * 32).credential_id
    segundo = Credentials("la-clave", "s" * 32).credential_id
    assert primero == segundo
    # Y distingue credenciales distintas, que es para lo que se usa.
    assert Credentials("otra-clave", "s" * 32).credential_id != primero


# --- Un solo worker (Fase 4) ---


def test_varios_workers_se_avisan_en_dev(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    """Byte guarda los runs y los nonces en memoria del proceso: con dos
    workers, el SSE no encuentra runs del otro y el "un solo uso" de los tokens
    vale una vez por worker.

    El Dockerfile fija `--workers 1`, pero `WEB_CONCURRENCY` lo pisa sin tocarlo
    y es lo que varios PaaS definen solos.
    """
    from api.main import _avisar_si_hay_varios_workers

    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    _avisar_si_hay_varios_workers("dev")
    assert "varios_workers" in caplog.text


def test_varios_workers_no_arrancan_en_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    """En dev avisar alcanza; en prod la garantía perdida es silenciosa y el
    síntoma —un token que a veces vale dos veces— es imposible de diagnosticar."""
    from api.main import _avisar_si_hay_varios_workers

    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY"):
        _avisar_si_hay_varios_workers("prod")


@pytest.mark.parametrize("valor", ["1", "", "no-es-un-numero"])
def test_un_worker_o_un_valor_raro_no_molestan(
    valor: str, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """El caso normal no tiene que decir nada, y un valor inválido tampoco
    puede impedir que Byte arranque."""
    from api.main import _avisar_si_hay_varios_workers

    monkeypatch.setenv("WEB_CONCURRENCY", valor)
    _avisar_si_hay_varios_workers("prod")  # no explota
    assert "varios_workers" not in caplog.text
