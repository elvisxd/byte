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
        json={"texto": UPWORK},
        headers={"Authorization": f"Bearer {CLAVE}"},
    )
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["sitio"] == "upwork"
    assert datos["analizadas"] == 1
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


def test_los_estaticos_llevan_version(cliente: TestClient) -> None:
    """Sin esto el navegador se queda con el `ofertas.js` de ayer y un despliegue
    nuevo no se ve. Pasó de verdad: la sección de alertas estaba desplegada y en
    pantalla no aparecía, que es el síntoma más caro de diagnosticar porque es
    idéntico a "el código está mal"."""
    html = cliente.get("/").text
    assert "/static/ofertas.js?v=" in html
    assert "/static/ofertas.css?v=" in html


def test_la_version_cambia_cuando_cambia_el_archivo(tmp_path) -> None:
    """Sale del mtime del estático y no de la fecha del despliegue: así el caché
    se rompe cuando el archivo cambió, y no en cada despliegue porque sí."""
    import os

    from empleo.paginas import pagina_con_version

    estaticos = tmp_path / "static"
    estaticos.mkdir()
    (estaticos / "x.js").write_text("hola", encoding="utf-8")
    plantillas = tmp_path / "templates"
    plantillas.mkdir()
    pagina = plantillas / "p.html"
    pagina.write_text('<script src="/static/x.js"></script>', encoding="utf-8")

    os.utime(estaticos / "x.js", (1000, 1000))
    antes = pagina_con_version(pagina)
    os.utime(estaticos / "x.js", (2000, 2000))
    assert pagina_con_version(pagina) != antes
    assert "?v=1000" in antes and "?v=2000" in pagina_con_version(pagina)
