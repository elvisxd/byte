"""Pegar una página entera y que salga una lista de a cuáles aplicar.

Los casos de acá son los que rompieron de verdad mientras se escribía el
parseo. Cada uno documenta una forma en que una página pegada se malinterpreta
en silencio — que es lo peor que puede pasar: una lista con aspecto correcto y
las ofertas partidas por la mitad.
"""

from tests.conftest import AUTH

LINKEDIN = """Skip to main content
LinkedIn
Home
My Network
Senior AI Engineer
Acme Corp
Remote (United States)
$180,000/yr - $220,000/yr
Promoted · 2 days ago · 12 applicants
Easy Apply
Staff Backend Engineer, LangGraph
Globex
Remote - Latin America
1 week ago · Over 200 applicants
Marketing Intern
Initech
New York, NY (On-site)
3 days ago · 45 applicants
Privacy Policy
© 2026 LinkedIn"""

UPWORK = """Senior AI Engineer for LangGraph RAG pipeline
Hourly: $60.00-$90.00
Posted 25 minutes ago
Proposals: Less than 5
Payment verified
$50K+ spent
We need someone to build retrieval augmented generation pipelines with Python, \
FastAPI and pgvector on AWS. Remote, anywhere in the world, long term.
WordPress theme tweak needed today
Fixed-price - Est. Budget: $50
Posted 6 days ago
Proposals: 20 to 50
Payment unverified
Small change to an existing WordPress theme, an hour or two for someone who knows PHP."""


def _pegar(cliente, texto: str, connects: int = 0) -> dict:
    respuesta = cliente.post(
        "/api/v1/ofertas/pegado", json={"texto": texto, "connects": connects}, headers=AUTH
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def test_el_menu_y_el_pie_de_la_pagina_no_entran_como_ofertas(cliente) -> None:
    """Se pega la página ENTERA, con "Saltar al contenido principal" y el aviso de
    cookies. Si eso entra, la lista arranca con basura y se deja de mirar."""
    datos = _pegar(cliente, LINKEDIN)
    titulos = [o["titulo"] for o in datos["ofertas"]]
    assert titulos == [
        "Senior AI Engineer",
        "Staff Backend Engineer, LangGraph",
        "Marketing Intern",
    ]
    assert all("LinkedIn" not in t and "Privacy" not in t for t in titulos)


def test_el_menu_pegado_arriba_no_se_come_la_primera_oferta(cliente) -> None:
    """El menú viene pegado a la primera tarjeta, sin renglón en blanco. Filtrando
    después de cortar, ese bloque empieza con "Saltar al contenido" y se descarta
    entero — perdiendo la oferta más nueva, que es la que más importa."""
    datos = _pegar(cliente, LINKEDIN)
    assert datos["ofertas"][0]["titulo"] == "Senior AI Engineer"
    assert datos["ofertas"][0]["empresa"] == "Acme Corp"


def test_una_oferta_de_upwork_no_se_parte_en_dos(cliente) -> None:
    """En Upwork la descripción viene DESPUÉS de los datos. Cortando por marca de
    fin, la tarjeta se partía y la segunda mitad quedaba titulada "Payment
    verified" — con su propio puntaje, como si fuera otra oferta."""
    datos = _pegar(cliente, UPWORK, connects=10)
    titulos = [o["titulo"] for o in datos["ofertas"]]
    assert len(titulos) == 2
    assert titulos[0] == "Senior AI Engineer for LangGraph RAG pipeline"
    assert not any("Payment" in t for t in titulos)


def test_los_connects_solo_se_reparten_si_es_upwork(cliente) -> None:
    """En LinkedIn postular es gratis: no hay presupuesto que repartir, y mostrar
    un costo en Connects ahí sería inventar una restricción que no existe."""
    upwork = _pegar(cliente, UPWORK, connects=10)
    assert upwork["sitio"] == "upwork"
    assert upwork["connects_gastados"] == 6
    assert [o for o in upwork["ofertas"] if o["aplicar"]]

    linkedin = _pegar(cliente, LINKEDIN, connects=10)
    assert linkedin["sitio"] == "linkedin"
    assert linkedin["connects_gastados"] == 0


def test_sin_descripcion_ordena_pero_no_decide(cliente) -> None:
    """Las tarjetas de LinkedIn traen título, empresa, lugar y postulantes, y nada
    más. Con eso el ORDEN vale —competencia y frescura son datos duros— pero decir
    "aplicá a esta" con un puntaje sacado de seis palabras sería inventar certeza."""
    datos = _pegar(cliente, LINKEDIN)
    assert datos["poca_informacion"] is True
    assert not any(o["aplicar"] for o in datos["ofertas"])
    # Pero sí ordena: la de 12 postulantes va antes que la de 200.
    assert datos["ofertas"][0]["competencia"] == 12

    con_descripcion = _pegar(cliente, UPWORK, connects=10)
    assert con_descripcion["poca_informacion"] is False


def test_pegar_cualquier_cosa_no_rompe(cliente) -> None:
    """Pegar mal es lo más común de todo. Tiene que devolver cero ofertas sin
    error, no un 500 en la cara."""
    datos = _pegar(cliente, "hola\nqué tal")
    assert datos["ofertas"] == []


def test_hace_falta_credencial(cliente) -> None:
    """El endpoint lee `perfil/busqueda.toml`, que es el criterio del usuario."""
    assert cliente.post("/api/v1/ofertas/pegado", json={"texto": "x"}).status_code == 401


def test_la_pagina_se_sirve_y_no_trae_nada_inline(cliente) -> None:
    """La CSP del proyecto prohíbe script y style inline. Una página con un
    `<script>` adentro se serviría igual y no correría: falla en el navegador y no
    en los tests, que es la peor combinación."""
    respuesta = cliente.get("/ofertas")
    assert respuesta.status_code == 200
    html = respuesta.text
    assert "ofertas.js" in html and "ofertas.css" in html
    assert "<script>" not in html
    assert "<style" not in html
