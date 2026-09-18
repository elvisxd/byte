"""La guía cambia según el sitio, que es todo el punto.

Un consejo que sirve para Upwork es malo para LinkedIn: en uno postular cuesta
Connects y en el otro es gratis. Si las dos guías terminaran diciendo lo mismo,
la sección sobraría — así que eso es justamente lo que se verifica.
"""

from empleo.criterio import Puntaje
from empleo.oferta import Oferta
from empleo.sitios import GUIAS, guia_de, medidas
from empleo.upwork import OfertaUpwork, Veredicto
from tests.conftest import AUTH


def _veredicto(
    *, propuestas: int | None = None, horas: float | None = None, verificado: bool | None = None
) -> Veredicto:
    entrada = OfertaUpwork(
        oferta=Oferta(
            fuente="pegado",
            id_externo="1",
            titulo="Senior AI Engineer",
            empresa="Acme",
            url="https://ejemplo/1",
            descripcion="",
        ),
        propuestas=propuestas,
        horas=horas,
        verificado=verificado,
    )
    puntaje = Puntaje(total=0, motivos=(), senales=(), terminos=())
    return Veredicto(entrada=entrada, puntaje=puntaje, ajuste=0, motivos=())


def test_upwork_y_linkedin_no_dan_la_misma_guia() -> None:
    upwork = guia_de("upwork", [])
    linkedin = guia_de("linkedin", [])

    assert upwork["filtros"] != linkedin["filtros"]
    assert upwork["pasos"] != linkedin["pasos"]
    # Lo que distingue a cada uno tiene que estar donde corresponde y no en el
    # otro: los Connects son de Upwork, y el "las tarjetas no traen descripción"
    # es de la pantalla de resultados de LinkedIn.
    assert "connects" in " ".join(upwork["pasos"]).lower()
    assert "connects" not in " ".join(linkedin["pasos"]).lower()
    assert "descripción" in " ".join(linkedin["pasos"]).lower()


def test_el_sitio_desconocido_no_inventa_filtros_de_nadie() -> None:
    """Con un sitio que no reconozco, decir "poné Payment verified" sería mandarlo
    a buscar una perilla que no existe."""
    generico = guia_de("monster", [])
    assert generico["nombre"] == GUIAS["generico"].nombre
    assert "payment verified" not in " ".join(generico["filtros"]).lower()


def test_las_medidas_cuentan_lo_pegado() -> None:
    veredictos = [
        _veredicto(propuestas=2, horas=3.0),
        _veredicto(propuestas=30, horas=5.0),
        _veredicto(propuestas=1, horas=200.0),
    ]
    texto = " ".join(medidas("upwork", veredictos))
    assert "Publicadas hace menos de 24 h: 2 de 3" in texto
    # Sólo la primera junta las dos condiciones: poca competencia Y fresca.
    assert "Lista corta —5 propuestas o menos Y menos de 24 h—: 1 de 3" in texto


def test_sin_el_dato_se_dice_que_falta_en_vez_de_contar_cero() -> None:
    """Contar cero se lee como "no hay ninguna fresca", que es una afirmación.
    No tener el dato es otra cosa y hay que decirla distinto."""
    texto = " ".join(medidas("linkedin", [_veredicto(), _veredicto()]))
    assert "Ninguna de las 2 trae cuándo se publicó" in texto
    assert "Ninguna de las 2 trae cuánta gente" in texto


def test_sin_ofertas_no_hay_medidas() -> None:
    assert medidas("upwork", []) == ()


def test_la_pagina_recibe_la_guia_del_sitio_que_se_pego(cliente) -> None:
    upwork = "Senior AI Engineer\nPosted 25 minutes ago\nProposals: Less than 5\nPayment verified"
    respuesta = cliente.post(
        "/api/v1/ofertas/pegado", json={"texto": upwork, "connects": 6}, headers=AUTH
    )
    assert respuesta.status_code == 200, respuesta.text
    guia = respuesta.json()["guia"]
    assert guia["nombre"] == "Upwork"
    assert guia["filtros"] and guia["pasos"]


def test_el_cubo_less_than_5_de_upwork_entra_en_la_lista_corta() -> None:
    """`Proposals: Less than 5` se lee pesimistamente como 5 —ver `_propuestas`—,
    y 5 es justo el cubo que produce el filtro que la guía recomienda poner.

    Con un `<` estricto, la medida contestaba "0 de 2 juntan las dos cosas" sobre
    una pantalla filtrada por ese mismísimo criterio. Se veía sólo ejecutándolo.
    """
    texto = " ".join(medidas("upwork", [_veredicto(propuestas=5, horas=0.5)]))
    assert "Lista corta —5 propuestas o menos Y menos de 24 h—: 1 de 1" in texto
