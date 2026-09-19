"""En qué quedó cada postulación, clasificado por reglas.

Los casos de acá son **correos reales del buzón**, no frases inventadas: Jobvite,
Ashby, Workday, Rippling, Wellfound, applytojob, monstergovt. Escribir las reglas
contra cómo uno se imagina que redactan los ATS es la forma segura de que fallen
el día que llega uno de verdad.
"""

import pytest

from empleo.postulaciones import Respuesta, bloque, clasificar

# (estado esperado, asunto, fragmento del cuerpo) — copiados de correos reales.
REALES = [
    (
        "rechazo",
        "Your application for Associate Power BI Developer",
        "After careful consideration, we've made the decision not to move forward "
        "with your application for this role.",
    ),
    (
        "rechazo",
        "Your interest in ML / AI Software Engineer at GM",
        "At this time, we've decided to move forward with other candidates.",
    ),
    (
        "rechazo",
        "An update from Nuel",
        "Thank you for your application to the Full Stack Engineer position at Nuel. "
        "Unfortunately, they've chosen to not move forward with your candidacy",
    ),
    (
        "accion",
        "Application received for Senior Software Engineer",
        "Thanks for applying to the Senior Software Engineer role at Scale Army. "
        "A video is required for this role. If you have not submitted yours yet",
    ),
    (
        "accion",
        "Action Required: ACNT Application - A190105505",
        "Your application requires additional information. Click here to find out "
        "what is needed. Be sure you review and resubmit your application",
    ),
    (
        "acuse",
        "Your application for Business Intelligence Manager at Resolver",
        "Thank you for taking the time to apply to Resolver. We have received a lot "
        "of interest in this position",
    ),
    (
        "acuse",
        "Thank you for applying to Onfleet",
        "Thank you for applying to Onfleet! We have received your application",
    ),
    (
        "acuse",
        "Application to Rec successfully submitted",
        "Your application has been submitted! If there's a match, we will make an "
        "email introduction.",
    ),
    (
        "",
        "New jobs: Full Stack Engineer at Infovision",
        "I've found 2 new jobs that might interest you!",
    ),
    (
        "",
        "You're on the list. Welcome to job alerts.",
        "Getting a job offer is even easier than before.",
    ),
    (
        "",
        "Elvis, you have a new prequalified offer",
        "Refresh your personalized loan options. NEW LOAN OFFER",
    ),
    ("", "Your working style results are in!", "Your working style assessment results are in."),
]


@pytest.mark.parametrize(("estado", "asunto", "cuerpo"), REALES)
def test_los_correos_reales_se_clasifican_bien(estado: str, asunto: str, cuerpo: str) -> None:
    assert clasificar(asunto, cuerpo) == estado


def test_un_rechazo_que_empieza_agradeciendo_sigue_siendo_rechazo() -> None:
    """Casi todos empiezan con "thanks for applying". Si ganara el acuse, cada
    rechazo se contaría como una postulación viva y la lista mentiría."""
    assert (
        clasificar(
            "Application Update",
            "Thanks for taking the time to apply. After reviewing your application, "
            "we will not be moving forward with your profile.",
        )
        == "rechazo"
    )


def test_un_acuse_que_pide_algo_es_accion() -> None:
    """El caso que originó todo esto: el correo de Scale Army es un acuse normal
    con una línea en el medio que dice que falta un video. Si se clasificara como
    acuse, no llegaría al teléfono — y la postulación queda parada once días."""
    assert (
        clasificar(
            "Application received", "Thanks for applying. A video is required for this role."
        )
        == "accion"
    )


def test_la_empresa_sale_del_dominio_y_no_del_asunto() -> None:
    """Los ATS mandan desde `no-reply@ashbyhq.com` con el nombre de la empresa
    sólo en el cuerpo: adivinarlo del asunto acierta a veces y miente el resto."""
    respuesta = Respuesta(
        id_mensaje="<1@x>",
        remitente="Talent <no-reply@ashbyhq.com>",
        asunto="Application Update",
        fecha="",
        estado="accion",
    )
    assert respuesta.empresa == "ashbyhq"


def test_sin_pendientes_no_hay_bloque() -> None:
    """Es lo que permite que el cazador siga callado cuando no hay nada."""
    assert bloque([]) == ""


def test_las_entrevistas_van_primero() -> None:
    accion = Respuesta("<1@x>", "a@kwanii.com", "Falta el video", "", "accion")
    entrevista = Respuesta("<2@x>", "b@mintmcp.com", "Agendemos una llamada", "", "entrevista")
    texto = bloque([accion, entrevista])
    assert texto.index("mintmcp") < texto.index("kwanii")
    assert "[entrevista]" in texto and "[pide algo]" in texto


_MARKETING_DE_WWR = """Set up your profile, save searches, and get job alerts - takes 5 min.
Four steps between you and your next role.
Complete My Profile
Hi Elvis, You signed up a few days ago, but your profile is still in setup mode.
1. Complete your profile
2. Set your job filters
3. Follow companies you want to work for.
4. Save jobs to apply to later. See something good but not ready to apply?
"""


def test_completar_el_perfil_de_un_portal_no_es_una_postulacion() -> None:
    """Llegó al teléfono de verdad, como "acción requerida" —la alerta que
    interrumpe SIEMPRE— por un correo de alta de usuario de We Work Remotely.

    Dos cosas lo dejaban pasar: `complete your profile` estaba en `ACCION`, y
    ACCION le ganaba al guardia de "esto no es una respuesta". Completar el
    perfil de un portal es alta de usuario, no algo que pida una postulación.
    """
    assert clasificar("Four steps left on your WWR profile", _MARKETING_DE_WWR) == ""


def test_apply_suelto_no_alcanza_para_ser_una_postulacion() -> None:
    """El mismo correo dice "Save jobs to apply to later" y "not ready to
    apply". Un patrón que aceptara "apply" a secas lo volvería a dejar pasar."""
    assert clasificar("Jobs for you", "Save jobs to apply to later. Not ready to apply?") == ""


def test_lo_que_sí_pide_algo_sigue_llegando() -> None:
    """El arreglo no puede llevarse puesto el caso que originó la alerta."""
    assert (
        clasificar(
            "Application received for Senior Software Engineer",
            "Thanks for applying to the Senior Software Engineer role at Scale Army. "
            "A video is required for this role.",
        )
        == "accion"
    )
