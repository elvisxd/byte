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


def _pegar(cliente, texto: str) -> dict:
    respuesta = cliente.post("/api/v1/ofertas/pegado", json={"texto": texto}, headers=AUTH)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def test_el_menu_y_el_pie_de_la_pagina_no_entran_como_ofertas(cliente) -> None:
    """Se pega la página ENTERA, con "Saltar al contenido principal" y el aviso de
    cookies. Si eso entra, se analiza basura como si fueran puestos.

    Se cuenta por `analizadas` y no por los títulos devueltos: desde que la
    página muestra sólo las elegidas, las descartadas no viajan. `analizadas` es
    lo que dice cuántas tarjetas se reconocieron, que es lo que este test mide."""
    datos = _pegar(cliente, LINKEDIN)
    assert datos["analizadas"] == 3


def test_una_oferta_de_upwork_no_se_parte_en_dos(cliente) -> None:
    """En Upwork la descripción viene DESPUÉS de los datos. Cortando por marca de
    fin, la tarjeta se partía y la segunda mitad quedaba titulada "Payment
    verified" — con su propio puntaje, como si fuera otra oferta."""
    datos = _pegar(cliente, UPWORK)
    assert datos["analizadas"] == 2
    # La buena se elige; la de WordPress no llega al mínimo y no vuelve.
    titulos = [o["titulo"] for o in datos["ofertas"]]
    assert titulos == ["Senior AI Engineer for LangGraph RAG pipeline"]
    assert not any("Payment" in t for t in titulos)


def test_solo_vuelven_las_elegidas(cliente) -> None:
    """La página muestra a cuáles aplicar, no un ranking. Las descartadas ni
    siquiera viajan: mandarlas para esconderlas sería poner en el navegador una
    lista que nadie va a mirar, y la razón de no mostrarlas es que no aportan.

    Pero el total sí viaja. Sin él, "0 elegidas" sería indistinguible de "no
    entendí lo que pegaste", que son cosas distintas y se arreglan distinto."""
    datos = _pegar(cliente, UPWORK)
    assert datos["analizadas"] == 2
    assert len(datos["ofertas"]) == 1
    # Y sin rastros del presupuesto, que se sacó de la página.
    assert "connects_gastados" not in datos
    assert "connects" not in datos["ofertas"][0]


def test_sin_descripcion_no_elige_ninguna(cliente) -> None:
    """Las tarjetas de LinkedIn traen título, empresa, lugar y postulantes, y nada
    más. Decir "aplicá a esta" con un puntaje sacado de seis palabras sería
    inventar una certeza, así que no se elige ninguna — y la página lo explica en
    vez de mostrar una lista vacía."""
    datos = _pegar(cliente, LINKEDIN)
    assert datos["poca_informacion"] is True
    assert datos["ofertas"] == []
    # Pero se leyeron: es lo que separa "no puedo decidir" de "no entendí nada".
    assert datos["analizadas"] == 3

    con_descripcion = _pegar(cliente, UPWORK)
    assert con_descripcion["poca_informacion"] is False


def test_pegar_cualquier_cosa_no_rompe(cliente) -> None:
    """Pegar mal es lo más común de todo. Tiene que devolver cero ofertas sin
    error, no un 500 en la cara."""
    datos = _pegar(cliente, "hola\nqué tal")
    assert datos["ofertas"] == []
    assert datos["analizadas"] == 0


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


# La pantalla de búsqueda de Upwork tal como sale hoy: el "Posted …" ABRE cada
# tarjeta, hay renglones en blanco DENTRO de ella —antes de "Skills", antes del
# pie del cliente— y el pie trae la reputación y el gasto histórico.
#
# El otro maquetado, con el título arriba del "Posted", es el de `UPWORK`: los
# dos tienen que funcionar, y por eso están los dos.
UPWORK_PANTALLA = """Posted 50 minutes ago
•
Proposals: 20 to 50
WhatsApp API Consultant
Hourly: $75-$200 - Expert - Est. Time: Less than 1 month
We need an expert on the Meta WhatsApp Cloud API, webhooks into a Node/TypeScript \
and Postgres backend. Long-term, ongoing collaboration.

Skills
WhatsApp
API Integration

Verified 
Payment verified
 
Rating is 5.0 out of 5.
 $300K+ spent 
  United States

Posted yesterday
•
Proposals: 20 to 50
Full-Stack EdTech Developer — Paid $500–$750 Trial Milestone
Fixed-price - Expert - Est. Budget: $750
This is intended to be the first phase of a larger monthly milestone-based project \
using React, Next.js, Node.js and PostgreSQL.

Skills
Full-Stack Development
React

Unverified 
Payment unverified
 
Rating is 0 out of 5.
 $0 spent 
  United States"""


def test_los_renglones_en_blanco_de_la_tarjeta_no_parten_la_oferta(cliente) -> None:
    """Upwork deja renglones vacíos DENTRO de cada tarjeta. Cortando ahí, una
    oferta se parte en tres y el pedazo con "$300K+ spent" queda sin título.

    Medido contra un pegado real de 6 ofertas: cortar por renglón en blanco daba
    12 bloques y cortar por "parece un título" daba 15 —las listas de skills son
    renglones cortos sin punto final, así que "Adobe Illustrator" abría una
    oferta—. Las dos entregaban una lista con aspecto correcto y los datos del
    cliente pegados a la oferta equivocada, que es el error caro acá.

    Se mide por `analizadas`, que es lo que cuenta las tarjetas reconocidas: las
    descartadas no vuelven desde que la página muestra sólo las elegidas."""
    datos = _pegar(cliente, UPWORK_PANTALLA)
    assert datos["analizadas"] == 2


def test_el_historial_del_cliente_queda_pegado_a_su_oferta(cliente) -> None:
    """Es el dato que más mueve la decisión —el que gastó $300K contrata; el de
    $0 puede no contratar nunca— y al partirse la tarjeta le sumaba puntos a un
    fragmento sin texto.

    Las dos tarjetas nombran stack parecido; la diferencia es el cliente. Que
    sobreviva sólo la de $300K verificado es lo que demuestra que el dato llegó
    a la oferta correcta."""
    datos = _pegar(cliente, UPWORK_PANTALLA)
    assert [o["titulo"] for o in datos["ofertas"]] == ["WhatsApp API Consultant"]
    assert datos["ofertas"][0]["gastado"] == 300_000
    assert datos["ofertas"][0]["verificado"] is True


def test_el_titulo_no_es_la_lista_de_skills_ni_el_pie_del_cliente(cliente) -> None:
    """Sin esto los títulos salían "Full-Stack Development", "Adobe Illustrator"
    o "Rating is 5.0 out of 5.". Una lista así es ilegible aunque el orden esté
    bien, porque no se puede saber a qué oferta corresponde cada línea."""
    datos = _pegar(cliente, UPWORK_PANTALLA)
    for oferta in datos["ofertas"]:
        assert (
            not oferta["titulo"]
            .lower()
            .startswith(("skills", "rating is", "verified", "unverified", "posted", "proposals"))
        )


def test_el_titulo_puede_nombrar_plata_sin_dejar_de_ser_titulo() -> None:
    """ "Paid $500–$750 Trial Milestone" es un título, no una línea de tarifa.
    Descartarlo por nombrar plata dejaba a la oferta titulada con el renglón
    siguiente, que era "Full-Stack Development" de la lista de skills.

    Va contra el parser y no contra el endpoint: esa oferta es de un cliente sin
    verificar y con $0 gastados, así que no llega a elegirse — y es justamente
    su título el que hay que comprobar."""
    from empleo.pegado import separar

    bloques, _ = separar(UPWORK_PANTALLA)
    assert any("$500" in b for b in bloques)
    # Y el pedazo con el título NO empieza por la lista de skills.
    assert not any(b.lstrip().lower().startswith("full-stack development") for b in bloques)


# Dos ofertas CON descripción, pero flojas: cliente sin verificar, $0 gastados,
# 50+ propuestas. Ninguna llega al mínimo.
FLOJAS_CON_DESCRIPCION = """Posted 6 days ago
Proposals: 50+
WordPress theme tweak needed today
Fixed-price - Est. Budget: $50
Small change to an existing WordPress theme, an hour or two for someone who knows PHP.
Unverified
Payment unverified
$0 spent

Posted 8 days ago
Proposals: 50+
Data entry assistant for spreadsheets
Fixed-price - Est. Budget: $30
Copy rows from PDFs into Google Sheets. No experience needed at all for this one.
Unverified
Payment unverified
$0 spent"""


def test_ninguna_sirve_no_se_confunde_con_no_hay_descripcion(cliente) -> None:
    """Son dos diagnósticos distintos y se arreglan distinto: uno se resuelve
    pegando de nuevo con las descripciones, el otro no se resuelve.

    El umbral medía la tarjeta ENTERA, y en Upwork la mitad son metadatos
    —"Posted 6 days ago", "Proposals: 50+", "$0 spent"—. Estas dos daban 213 de
    promedio contra un mínimo de 220, así que la página pedía las descripciones
    teniéndolas delante y escondía la razón real: que ninguna vale."""
    datos = _pegar(cliente, FLOJAS_CON_DESCRIPCION)
    assert datos["analizadas"] == 2
    assert datos["ofertas"] == []
    assert datos["poca_informacion"] is False


def test_lo_que_se_mide_es_la_prosa_y_no_los_metadatos(cliente) -> None:
    """La contracara del test de arriba: una tarjeta que de verdad viene sin
    descripción tiene que seguir detectándose, o el arreglo del umbral
    convertiría el problema en el opuesto."""
    sin_prosa = """Posted 2 days ago
Proposals: 20 to 50
Senior Backend Engineer
Hourly: $50-$90 - Expert
Payment verified
$10K+ spent"""
    datos = _pegar(cliente, sin_prosa)
    assert datos["poca_informacion"] is True
    assert datos["ofertas"] == []
