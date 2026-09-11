"""Salud, cabeceras de seguridad y formato de errores."""

from fastapi.testclient import TestClient

from tests.conftest import AUTH


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
