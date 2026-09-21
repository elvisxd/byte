"""En qué quedó cada postulación, clasificado por reglas.

Los casos de acá son **correos reales del buzón**, no frases inventadas: Jobvite,
Ashby, Workday, Rippling, Wellfound, applytojob, monstergovt. Escribir las reglas
contra cómo uno se imagina que redactan los ATS es la forma segura de que fallen
el día que llega uno de verdad.
"""

import pytest

from empleo.postulaciones import (
    DE_POSTULACION,
    Respuesta,
    bloque,
    clasificar,
    de_un_ats,
    empresa_del_asunto,
    es_probable_estafa,
    inventario,
    linea,
    sospechas,
)

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
        "alerta",
        "New jobs: Full Stack Engineer at Infovision",
        "I've found 2 new jobs that might interest you!",
    ),
    (
        "alerta",
        "You're on the list. Welcome to job alerts.",
        "Getting a job offer is even easier than before.",
    ),
    (
        "alerta",
        "Elvis, you have a new prequalified offer",
        "Refresh your personalized loan options. NEW LOAN OFFER",
    ),
    (
        "alerta",
        "Your working style results are in!",
        "Your working style assessment results are in.",
    ),
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
    # Lo que importa es que NO sea una respuesta a una postulación tuya. Desde
    # que nada se descarta en silencio tiene un estado igual —"alerta"— y eso no
    # debilita el test: `accion` es lo que interrumpe, y sigue afuera.
    assert clasificar("Four steps left on your WWR profile", _MARKETING_DE_WWR) not in (
        *DE_POSTULACION,
        "contacto",
    )


def test_apply_suelto_no_alcanza_para_ser_una_postulacion() -> None:
    """El mismo correo dice "Save jobs to apply to later" y "not ready to
    apply". Un patrón que aceptara "apply" a secas lo volvería a dejar pasar."""
    assert clasificar("Jobs for you", "Save jobs to apply to later. Not ready to apply?") not in (
        *DE_POSTULACION,
        "contacto",
    )


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


# --- Nada se descarta en silencio ---


def test_todo_correo_sale_con_un_estado() -> None:
    """Antes, lo que el clasificador no reconocía devolvía "" y el lector lo
    tiraba: ni contado, ni registrado, ni visible. De los sesenta correos que se
    miran por vuelta no había forma de saber cuántos caían ahí ni de qué eran,
    así que un ATS que cambiara la plantilla dejaba de verse y nadie se
    enteraba. Ahora el cajón tiene nombre y se puede medir.
    """
    assert clasificar("Your invoice is ready", "Your monthly invoice is available.") == "otro"
    assert clasificar("", "") == "otro"


def test_un_reclutador_de_agencia_no_es_una_empresa_escribiendote() -> None:
    """`corp to corp`, `C2C` y `W2` son la firma del cuerpo de intermediación
    que revende horas: no aparecen nunca en el correo de la empresa que
    contrata. Se mira ANTES que el contacto directo porque la agencia escribe
    las dos cosas —"vi tu perfil" y "corp to corp"—, y al revés todas entraban
    como empresas escribiéndote."""
    estado = clasificar(
        "Senior Full Stack Developer - Remote - C2C",
        "Hi, one of our clients is looking for a developer. Corp to corp or W2. "
        "I came across your profile. What is your hourly rate?",
    )

    assert estado == "reclutador"


def test_una_empresa_que_te_escribe_por_un_puesto_es_un_contacto() -> None:
    """La mitad buena del correo frío, y hasta ahora se tiraba entera."""
    estado = clasificar(
        "Senior Full-Stack Engineer at Cohere",
        "Hi Elvis, I came across your GitHub and your experience with LangGraph "
        "stood out. Would you be interested in talking about our team?",
    )

    assert estado == "contacto"


# --- Señales de estafa ---


def test_la_estafa_clasica_enciende_varias_senales() -> None:
    """Las denuncias por estafa de empleo a la FTC pasaron de ~35.000 a más de
    105.000 al año entre 2020 y 2024, y las pérdidas declaradas de 90 a más de
    513 millones de dólares. Este correo tiene la forma que describen: plata
    grande sin decir el trabajo, entrevista por Telegram, y que compres el
    equipo vos.
    """
    senales = sospechas(
        "recruiter.amazon.hiring@gmail.com",
        "",
        "dkim=pass spf=pass dmarc=pass",
        "Amazon Remote Data Entry Position",
        "We are hiring for a remote position. Pay is $350 per day, no experience "
        "is required. Please add me on Telegram for the interview. You will need "
        "to purchase equipment; we will send you a check.",
    )

    assert "pide plata por adelantado" in senales
    assert "la entrevista es por chat" in senales
    assert "escribe desde un correo gratuito" in senales
    assert es_probable_estafa(senales)


def test_el_reply_to_a_un_correo_gratuito_es_el_truco_clasico() -> None:
    """El `From` imita a la empresa y el `Reply-To` manda tu respuesta a otro
    lado. Los ATS legítimos también usan Reply-To distinto, así que sólo se
    anota cuando el destino es un correo gratuito."""
    senales = sospechas(
        "careers@stripe-talent.com", "stripejobs99@outlook.com", "", "Opportunity", "Hi there"
    )

    assert any("responder iría a outlook.com" in s for s in senales)


def test_un_reply_to_a_otro_dominio_corporativo_no_es_sospechoso() -> None:
    """Greenhouse, Ashby y Workday mandan en nombre de la empresa y contestan a
    otro dominio todo el tiempo. Marcar eso sería marcar media bandeja."""
    senales = sospechas(
        "no-reply@us.greenhouse-mail.io", "jobs@cohere.com", "", "Application received", "Hi"
    )

    assert not any("responder iría" in s for s in senales)


def test_gmail_ya_verifico_la_autenticacion_y_se_le_cree() -> None:
    """Gmail escribe el resultado de SPF, DKIM y DMARC en cada correo que
    recibe. Tirar esa cabecera sería repetir a mano un trabajo ya hecho."""
    senales = sospechas(
        "careers@stripe.com",
        "",
        "mx.google.com; dkim=fail header.i=@stripe.com; spf=fail; dmarc=fail",
        "Opportunity at Stripe",
        "Hi",
    )

    assert any("no pasa la autenticación" in s for s in senales)
    assert es_probable_estafa(senales)


def test_una_sola_senal_blanda_no_silencia_una_oportunidad() -> None:
    """La división entre duras y blandas es la decisión de diseño de todo
    esto. Un reclutador independiente legítimo escribe desde Gmail y una
    agencia legítima pone "$85/hr" en el asunto: perder ese correo cuesta más
    que ver una estafa marcada y decidirlo vos.
    """
    assert not es_probable_estafa(("escribe desde un correo gratuito",))
    assert not es_probable_estafa(("promete plata sin decir el trabajo",))
    # Dos blandas ya son un patrón, no una coincidencia.
    assert es_probable_estafa(
        ("escribe desde un correo gratuito", "promete plata sin decir el trabajo")
    )
    # Y una dura sola alcanza: ninguna empresa te pide plata.
    assert es_probable_estafa(("pide plata por adelantado",))


def test_un_contacto_con_estafa_no_interrumpe_pero_no_desaparece() -> None:
    """Por el correo frío entra la mitad de las estafas de empleo, y un canal
    que te despierta con fraude es un canal que dejás de abrir. Pero tirarlo
    sería volver al problema de antes: sigue en `--correos`, con las señales a
    la vista."""
    estafa = Respuesta(
        "<1@x>",
        "hr@gmail.com",
        "Remote job",
        "",
        "contacto",
        ("pide plata por adelantado",),
    )
    buena = Respuesta("<2@x>", "sarah@cohere.com", "Role at Cohere", "", "contacto", ())

    assert estafa.interrumpe is False
    assert buena.interrumpe is True


def test_un_acuse_de_verdad_interrumpe_aunque_tenga_una_senal() -> None:
    """La conversación ya existe: postulaste vos. Silenciar un "falta el video"
    porque el ATS manda desde un dominio raro es perder la postulación."""
    r = Respuesta("<1@x>", "no-reply@x.com", "Video required", "", "accion", ("lo que sea",))

    assert r.interrumpe is True


def test_el_inventario_muestra_el_cajon_de_lo_no_reconocido() -> None:
    """Es de lo único que sirve: verlo crecer."""
    rs = [
        Respuesta("<1@x>", "a@digitalocean.com", "Your invoice is ready", "", "otro", ()),
        Respuesta("<2@x>", "b@cohere.com", "Thanks for applying", "", "acuse", ()),
    ]

    texto = inventario(rs)

    assert "Buzón — 2 correos" in texto
    assert "sin reconocer (1)" in texto
    assert "Your invoice is ready" in texto


def test_el_reply_to_a_un_gmail_alcanza_solo_para_no_despertarte() -> None:
    """Segunda lectura del mismo archivo. El comentario decía "las dos que
    salen del sobre del correo" y sólo agregaba una: la señal del `Reply-To`
    estaba contada como blanda, así que sola no silenciaba nada.

    Y es el truco de suplantación entero en una línea —el remitente imita a la
    empresa y tu respuesta se va a otro lado—, o sea lo más duro que hay acá.
    Medido antes del arreglo: ese correo despertaba el teléfono.
    """
    senales = sospechas(
        "careers@stripe-talent.com",
        "stripejobs99@outlook.com",
        "dkim=pass spf=pass dmarc=pass",
        "Exciting opportunity at Stripe",
        "Hi, we would like to talk about a role.",
    )

    assert len(senales) == 1
    assert es_probable_estafa(senales)
    assert (
        Respuesta("<1@x>", "careers@stripe-talent.com", "x", "", "contacto", senales).interrumpe
        is False
    )


def test_el_cajon_cuenta_bien_lo_que_no_muestra() -> None:
    """`otro` muestra el doble que los demás, y el resto se descontaba contra
    `por_tipo`: con doce correos mostraba diez y decía "+7 más". Equivocar ese
    número justo en el cajón —cuya única función es contar bien lo que no se
    reconoció— es equivocarlo donde más importa.
    """
    rs = [Respuesta(f"<{n}@x>", f"a@d{n}.com", f"Asunto {n}", "", "otro", ()) for n in range(12)]

    texto = inventario(rs, por_tipo=5)

    assert "sin reconocer (12)" in texto
    assert "(+2 más)" in texto
    assert sum(1 for linea in texto.splitlines() if linea.startswith("  d")) == 10


# --- Los acuses que nunca dicen "application" ------------------------------


def test_un_acuse_que_habla_de_tu_curriculo_y_no_de_tu_postulacion() -> None:
    """Correo real del 19/09. La compuerta `HABLA_DE_POSTULACION` pedía la
    palabra "application" y esta plantilla de JazzHR no la dice nunca: dice
    "received your resume" en el asunto e "interest in employment" en el cuerpo.

    Caía en `otro`, o sea que la postulación a SeedTrust quedaba sin rastro y
    el seguimiento la contaba como una empresa a la que NO postulaste. Que es
    lo contrario de lo que pasó.
    """
    asunto = "Elvis, we've received your resume"
    cuerpo = (
        "Hello Elvis, Thank you for your interest in employment at SeedTrust. "
        "If your qualifications match our needs, we will contact you to learn "
        "more about your fit in this position."
    )

    assert clasificar(asunto, cuerpo) == "acuse"


def test_el_acuse_que_no_nombra_ni_la_postulacion_ni_el_curriculo() -> None:
    """Correo real del 08/09, la misma plantilla con otras palabras. Acá no hay
    nada que agarrar en el texto: ni "application", ni "resume", ni "applying".
    Sólo "explore opportunities with", que lo escribe cualquier boletín.

    Este es el que necesita el remitente, y es la razón de `de_un_ats()`: a
    `noreply@applytojob.com` no le escribe nadie que no sea un ATS.
    """
    asunto = "Thank you for your interest in Morning Star"
    cuerpo = (
        "Dear Elvis, Thank you for investing your time to explore opportunities "
        "with Morning Star. This is an automated message to let you know we got it."
    )

    assert clasificar(asunto, cuerpo) == "otro"
    assert clasificar(asunto, cuerpo, "noreply@applytojob.com") == "acuse"


def test_el_marketing_que_agradece_tu_interes_sigue_sin_ser_un_acuse() -> None:
    """El reverso, y es el que importa: "thank you for your interest" lo escribe
    cualquiera. Correos reales de Walmart y Pinterest del 20/09.

    Si el remitente no es un ATS, la compuerta sigue en pie y el correo NO entra
    como respuesta a una postulación tuya. Lo que abre la puerta es quién lo
    manda, no la frase.
    """
    cuerpo = "Thank you for your interest in our weekly savings! Shop now."

    assert clasificar("Your weekly Walmart deals", cuerpo, "newsletters@em.walmart.com") != "acuse"
    assert clasificar("Ideas for you", cuerpo, "recommendations@discover.pinterest.com") != "acuse"


def test_los_ats_mandan_desde_un_subdominio_por_cliente() -> None:
    """El dominio se mira por sufijo porque el ATS le da a cada cliente el suyo.
    Los tres primeros son remitentes reales del buzón.
    """
    assert de_un_ats("no-reply@nara-health.breezy-mail.com")
    assert de_un_ats("activision@myworkday.com")
    assert de_un_ats("no-reply@us.greenhouse-mail.io")
    assert de_un_ats("recruiting+438383378-751d7eb6@applytojob.com")


def test_un_dominio_parecido_no_es_un_ats() -> None:
    """La comparación es por sufijo de dominio con el punto puesto: si fuera un
    `endswith` pelado, cualquiera que registre `malicioso-lever.co` entraría
    como plataforma de reclutamiento y se saltaría la compuerta.
    """
    assert not de_un_ats("alguien@malicioso-lever.co")
    assert not de_un_ats("alguien@greenhouse-mail.io.estafa.com")
    assert not de_un_ats("talent@ibm.com")
    # Y el nombre para mostrar no decide nada: lo que cuenta es lo que hay
    # después de la ÚLTIMA arroba, que es la dirección de verdad.
    assert not de_un_ats('"no-reply@ashbyhq.com" <estafa@dominio-malo.com>')
    assert not de_un_ats("isabella.holmes@lensa.com")
    assert not de_un_ats("")


# --- De qué empresa es el correo, cuando el remitente no lo dice ------------


@pytest.mark.parametrize(
    ("asunto", "empresa"),
    [
        # Todos son asuntos REALES del buzón, de 14 días.
        ("Thanks for applying to Cohere!", "Cohere"),
        ("Thank you for applying to NMI", "NMI"),
        ("Thank you for applying to Sourcegraph!", "Sourcegraph"),
        ("Thank you for applying to Onfleet", "Onfleet"),
        ("Thanks for applying to MintMCP!", "MintMCP"),
        ("Thanks for applying to Clera!", "Clera"),
        ("Thank you for your application to Blackpoint!", "Blackpoint"),
        ("Thanks for your application to Hiive", "Hiive"),
        ("We have received your application to Ascend Partner Services", "Ascend Partner Services"),
        ("Security code for your application to Stripe", "Stripe"),
        ("Security code for your application to Gusto, Inc.", "Gusto"),
        ("Thank you for your interest in StackAdapt!", "StackAdapt"),
        ("Thank you for your interest in Morning Star", "Morning Star"),
        ("Your application for Sr. Software Engineer, Web at Pinterest", "Pinterest"),
        ("Application Received - Engineering Manager, Platform  at Render", "Render"),
        ("Your application for Business Intelligence Manager at Resolver", "Resolver"),
        ("SeedTrust - Senior Software Engineer (Remote - US Based)", "SeedTrust"),
        ("Sticker Mule application", "Sticker Mule"),
        ("We've Got Your Activision Blizzard King Application", "Activision Blizzard King"),
        (
            "Update on your Morningstar Job Application Senior Software Engineer- Applied AI",
            "Morningstar",
        ),
        # El apóstrofo y la coletilla societaria se caen sin cambiar el nombre.
        ("Thank you for applying to Cresta's Engineering Team!", "Cresta"),
        # El bilingüe: gana lo que está antes de la barra.
        ("Thank you for applying to Shakepay | Merci d'avoir soumis votre candidate", "Shakepay"),
    ],
)
def test_la_empresa_sale_del_asunto(asunto: str, empresa: str) -> None:
    """El remitente de un ATS no nombra a la empresa; el asunto sí.

    Medido: de diez correos de postulación del buzón real, ocho no se podían
    atar a ninguna oferta por esto.
    """
    assert empresa_del_asunto(asunto) == empresa


@pytest.mark.parametrize(
    "asunto",
    [
        # También reales, y en todos la respuesta correcta es "no se sabe".
        "Application Update",
        "Thank you for your application!",
        "Complete your application for Full Stack Engineer",
        "Elvis, we've received your resume",
        "Application received for Senior Software Engineer",
        "Your application for Senior AI Product Engineer  has been received",
        "Your application for Associate Power BI Developer",
        "Thank you for applying for Associate Power BI Developer",
        "I found some career options for you",
        "Hey Elvis - welcome to Simplify!",
    ],
)
def test_cuando_el_asunto_no_nombra_a_nadie_no_se_inventa(asunto: str) -> None:
    """Devolver "" es un resultado válido y NO un fallo.

    Adivinar acá sería peor que no saber: un acuse atado a la empresa
    equivocada dice que tenés un proceso abierto donde no lo tenés. Un puesto
    —"Full Stack Engineer", "Associate Power BI Developer"— no es una empresa.
    """
    assert empresa_del_asunto(asunto) == ""


# --- El nombre que sale en el aviso del teléfono ----------------------------


def test_el_aviso_nombra_a_la_empresa_y_no_al_ats() -> None:
    """Aviso REAL que llegó al teléfono el 21/09:

        [pide algo] breezy-mail — Complete your application for Full Stack Engineer

    "breezy-mail" es el ATS. La empresa es Nara Health, y está en el
    subdominio: `no-reply@nara-health.breezy-mail.com`. El asunto de ese correo
    no nombra a nadie, así que el subdominio es lo único que queda.
    """
    respuesta = Respuesta(
        "<1@x>",
        "no-reply@nara-health.breezy-mail.com",
        "Complete your application for Full Stack Engineer",
        "",
        "accion",
        (),
    )

    assert respuesta.empresa == "nara-health"
    assert "breezy-mail" not in linea(respuesta)


def test_el_aviso_prefiere_el_asunto_al_dominio() -> None:
    """Correo real de Greenhouse. El subdominio es "us" —una región, no una
    empresa— pero el asunto sí nombra a Stripe.
    """
    respuesta = Respuesta(
        "<2@x>",
        "no-reply@us.greenhouse-mail.io",
        "Security code for your application to Stripe",
        "",
        "accion",
        (),
    )

    assert "Stripe" in linea(respuesta)


def test_un_subdominio_de_region_no_es_una_empresa() -> None:
    """El límite del arreglo de arriba. Greenhouse manda desde
    `us.greenhouse-mail.io`: si el subdominio entrara siempre, el aviso diría
    que postulaste a una empresa llamada "us".
    """
    respuesta = Respuesta(
        "<3@x>", "no-reply@us.greenhouse-mail.io", "Application Update", "", "rechazo", ()
    )

    assert respuesta.empresa == "greenhouse-mail"


def test_cuando_no_lo_nombra_nadie_se_dice_el_ats_y_no_se_inventa() -> None:
    """Ashby manda desde el dominio pelado y este asunto no nombra a nadie. El
    aviso dice "ashbyhq", que al menos te dice dónde buscarlo en el buzón.

    El cuerpo del correo SÍ nombra a la empresa, y aun así no se usa: `linea()`
    no mira el cuerpo a propósito —es texto de terceros— y esa decisión no se
    cambia por comodidad.
    """
    respuesta = Respuesta(
        "<4@x>",
        "no-reply@ashbyhq.com",
        "Application received for Senior Software Engineer",
        "",
        "accion",
        (),
    )

    assert respuesta.empresa == "ashbyhq"
