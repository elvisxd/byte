"""Conversaciones: CRUD, paginación y título automático."""

from fastapi.testclient import TestClient

from tests.conftest import AUTH, nueva_conversacion


def test_crear_y_listar(cliente: TestClient) -> None:
    primera = nueva_conversacion(cliente)
    segunda = cliente.post(
        "/api/v1/conversations", json={"title": "Con título"}, headers=AUTH
    ).json()
    assert segunda["title"] == "Con título"

    listado = cliente.get("/api/v1/conversations", headers=AUTH).json()
    ids = [item["id"] for item in listado["items"]]
    # Orden por updated_at desc: la más nueva primero.
    assert ids == [segunda["id"], primera]
    assert listado["next_cursor"] is None


def test_paginacion_por_cursor(cliente: TestClient) -> None:
    for _ in range(3):
        nueva_conversacion(cliente)
    primera_pagina = cliente.get("/api/v1/conversations", params={"limit": 2}, headers=AUTH).json()
    assert len(primera_pagina["items"]) == 2
    assert primera_pagina["next_cursor"]

    segunda_pagina = cliente.get(
        "/api/v1/conversations",
        params={"limit": 2, "cursor": primera_pagina["next_cursor"]},
        headers=AUTH,
    ).json()
    assert len(segunda_pagina["items"]) == 1
    assert segunda_pagina["next_cursor"] is None


def test_titulo_sale_del_primer_mensaje(cliente: TestClient) -> None:
    conversacion = nueva_conversacion(cliente)
    cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "Buscá la última versión de FastAPI"},
        params={"wait": True},
        headers=AUTH,
    )
    detalle = cliente.get(f"/api/v1/conversations/{conversacion}", headers=AUTH).json()
    assert detalle["title"] == "Buscá la última versión de FastAPI"


def test_detalle_oculta_mensajes_de_herramienta(cliente: TestClient) -> None:
    conversacion = nueva_conversacion(cliente)
    cliente.post(
        f"/api/v1/conversations/{conversacion}/messages",
        json={"content": "hola"},
        params={"wait": True},
        headers=AUTH,
    )
    visible = cliente.get(f"/api/v1/conversations/{conversacion}", headers=AUTH).json()
    assert [m["role"] for m in visible["messages"]] == ["user", "assistant"]

    completo = cliente.get(
        f"/api/v1/conversations/{conversacion}",
        params={"include_tool_messages": True},
        headers=AUTH,
    ).json()
    assert len(completo["messages"]) >= 2


def test_renombrar(cliente: TestClient) -> None:
    conversacion = nueva_conversacion(cliente)
    respuesta = cliente.patch(
        f"/api/v1/conversations/{conversacion}", json={"title": "Nuevo"}, headers=AUTH
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["title"] == "Nuevo"


def test_borrar(cliente: TestClient) -> None:
    conversacion = nueva_conversacion(cliente)
    assert cliente.delete(f"/api/v1/conversations/{conversacion}", headers=AUTH).status_code == 204
    assert cliente.get(f"/api/v1/conversations/{conversacion}", headers=AUTH).status_code == 404
    assert cliente.delete(f"/api/v1/conversations/{conversacion}", headers=AUTH).status_code == 404


def test_mensaje_en_conversacion_inexistente(cliente: TestClient) -> None:
    respuesta = cliente.post(
        "/api/v1/conversations/no-existe/messages", json={"content": "hola"}, headers=AUTH
    )
    assert respuesta.status_code == 404


def test_herramientas_listadas(cliente: TestClient) -> None:
    respuesta = cliente.get("/api/v1/tools", headers=AUTH)
    assert respuesta.json() == {"tools": [{"name": "web_search", "source": "builtin"}]}
