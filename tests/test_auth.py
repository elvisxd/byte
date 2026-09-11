"""Credenciales: API key, cookie de sesión y events_token."""

from collections.abc import Callable

from fastapi.testclient import TestClient

from api.security import SESSION_COOKIE
from tests.conftest import API_KEY, AUTH, nueva_conversacion


def test_api_key_invalida(cliente: TestClient) -> None:
    respuesta = cliente.get("/api/v1/conversations", headers={"X-API-Key": "otra"})
    assert respuesta.status_code == 401


def test_sesion_rechaza_clave_mala(cliente: TestClient) -> None:
    respuesta = cliente.post("/api/v1/session", json={"api_key": "incorrecta"})
    assert respuesta.status_code == 401
    assert SESSION_COOKIE not in respuesta.cookies


def test_sesion_por_cookie_httponly(cliente: TestClient) -> None:
    respuesta = cliente.post("/api/v1/session", json={"api_key": API_KEY})
    assert respuesta.status_code == 204
    cookie = respuesta.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie.replace("samesite", "SameSite")
    # Con la cookie puesta, el cliente ya puede operar sin el header.
    assert cliente.get("/api/v1/conversations").status_code == 200


def test_cerrar_sesion(cliente: TestClient) -> None:
    cliente.post("/api/v1/session", json={"api_key": API_KEY})
    assert cliente.delete("/api/v1/session").status_code == 204
    cliente.cookies.clear()
    assert cliente.get("/api/v1/conversations").status_code == 401


def test_events_token_es_de_un_solo_uso(cliente: TestClient) -> None:
    conversacion = nueva_conversacion(cliente)
    aceptado = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "hola"},
        headers=AUTH,
    ).json()
    url = f"/api/v1{aceptado['events_url'].removeprefix('/api/v1')}"
    token = aceptado["events_token"]

    primero = cliente.get(url, params={"token": token})
    assert primero.status_code == 200
    segundo = cliente.get(url, params={"token": token})
    assert segundo.status_code == 401


def test_events_token_no_sirve_para_otro_run(crear_cliente: Callable[..., TestClient]) -> None:
    cliente = crear_cliente()
    conversacion = nueva_conversacion(cliente)
    primero = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "uno"},
        headers=AUTH,
    ).json()
    segundo = cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "dos"},
        headers=AUTH,
    ).json()

    respuesta = cliente.get(
        f"/api/v1/runs/{segundo['run_id']}/events", params={"token": primero["events_token"]}
    )
    assert respuesta.status_code == 401


def test_sse_sin_credencial_ni_token(cliente: TestClient) -> None:
    assert cliente.get("/api/v1/runs/run_inexistente/events").status_code == 401


def test_run_de_otra_credencial_no_existe(cliente: TestClient) -> None:
    # Con credencial válida, un run desconocido es 404 (no se filtra su existencia).
    assert cliente.get("/api/v1/runs/run_inexistente", headers=AUTH).status_code == 404
