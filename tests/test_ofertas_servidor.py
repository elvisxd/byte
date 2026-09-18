"""El servicio suelto de la página de ofertas.

Va a tener una URL pública, así que lo que se cuida acá no es que funcione: es
que no funcione de más. Un endpoint abierto en internet que parsea texto
arbitrario es una invitación, y el momento de descubrirlo no es leyendo los logs
de Railway.
"""

import pytest
from fastapi.testclient import TestClient

from empleo.servidor import SinClave, crear_app

CLAVE = "clave-de-prueba-larga"

UPWORK = """Senior AI Engineer for LangGraph RAG pipeline
Hourly: $60.00-$90.00
Posted 25 minutes ago
Proposals: Less than 5
Payment verified
$50K+ spent
Build retrieval augmented generation pipelines with Python, FastAPI and pgvector on AWS."""


@pytest.fixture
def cliente() -> TestClient:
    return TestClient(crear_app(clave=CLAVE))


def test_sin_clave_no_arranca_en_vez_de_quedar_abierto() -> None:
    """Fallar cerrado y ruidoso. Un despliegue al que se le olvidó la variable
    tiene que caerse en el arranque y verse en los logs, no quedar sirviendo a
    quien encuentre la URL."""
    with pytest.raises(SinClave):
        crear_app(clave="")


def test_el_endpoint_rechaza_sin_credencial(cliente: TestClient) -> None:
    """Es la puerta de entrada y está en internet."""
    assert cliente.post("/api/v1/ofertas/pegado", json={"texto": "x"}).status_code == 401
    mala = cliente.post(
        "/api/v1/ofertas/pegado",
        json={"texto": "x"},
        headers={"Authorization": "Bearer otra"},
    )
    assert mala.status_code == 401


def test_con_la_clave_analiza_igual_que_la_api_grande(cliente: TestClient) -> None:
    """Las dos rutas tienen que dar lo mismo: si divergen, el resultado depende
    de desde dónde entraste, que es la peor clase de sorpresa."""
    respuesta = cliente.post(
        "/api/v1/ofertas/pegado",
        json={"texto": UPWORK, "connects": 10},
        headers={"Authorization": f"Bearer {CLAVE}"},
    )
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["sitio"] == "upwork"
    assert datos["connects_gastados"] == 6
    assert datos["ofertas"][0]["titulo"].startswith("Senior AI Engineer")


def test_la_ruta_es_la_misma_que_en_la_api_grande(cliente: TestClient) -> None:
    """`ofertas.js` es UN archivo servido por los dos. Si acá la ruta fuera otra,
    habría que mantener dos copias del mismo JS — y divergen."""
    assert cliente.post("/api/v1/ofertas/pegado", json={"texto": "x"}).status_code == 401


def test_la_salud_no_pide_credencial(cliente: TestClient) -> None:
    """Railway la consulta para saber si el contenedor está vivo, y no dice nada
    que no se sepa por el hecho de que la URL responda."""
    assert cliente.get("/salud").json() == {"status": "ok"}


def test_no_expone_la_documentacion(cliente: TestClient) -> None:
    """En una URL pública, /docs es un mapa de la superficie para quien tantea."""
    assert cliente.get("/docs").status_code == 404


def test_las_alertas_se_sirven_por_la_misma_ruta(cliente: TestClient) -> None:
    """Mismo motivo que el pegado: `ofertas.js` es UN archivo para los dos lados,
    así que la ruta de las alertas tiene que existir acá con el mismo nombre."""
    respuesta = cliente.get("/api/v1/ofertas/alertas", headers={"Authorization": f"Bearer {CLAVE}"})
    assert respuesta.status_code == 200, respuesta.text
    plataformas = [a["plataforma"] for a in respuesta.json()["alertas"]]
    assert "LinkedIn" in plataformas and "Upwork" in plataformas


def test_las_alertas_tambien_piden_clave(cliente: TestClient) -> None:
    """Es una URL pública: todo lo que sirva el criterio va detrás de la clave."""
    assert cliente.get("/api/v1/ofertas/alertas").status_code == 401
