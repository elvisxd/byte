"""En qué quedó cada postulación, leído de tu propio buzón.

Postularse es la mitad barata. La cara es **no perder** lo que ya postulaste: un
"falta el video" que se lee once días tarde es una postulación regalada, y un
rechazo que no registrás es una empresa a la que volvés a escribir.

Esto lee el correo por IMAP —el mismo camino que ya usa la fuente de LinkedIn,
con la misma contraseña de aplicación y en **solo lectura**: no marca, no mueve
ni borra nada— y clasifica cada mensaje por reglas. No lo decide un modelo: las
frases son fijas, las escribió gente que usa plantillas, y una clasificación que
cambia sola entre corridas no se puede revisar.

**Nada se descarta en silencio.** Todo correo sale con un estado, aunque sea
`otro`. Antes, lo que no se reconocía devolvía "" y el lector lo tiraba sin
contarlo: de los sesenta que se miran por vuelta no había forma de saber cuántos
caían ahí ni de qué eran, y un ATS que cambiara la plantilla dejaba de verse sin
que nadie se enterara. `inventario()` —y `--correos`— muestran el reparto.

**Lo que interrumpe es poco a propósito.** Lo que pide algo tuyo, una entrevista
y un contacto directo por un puesto. Los rechazos, los acuses, las alertas y las
agencias se cuentan y se guardan sin despertarte: cinco "gracias por postularte"
por semana son la forma más rápida de que dejes de abrir el canal.

**Y hay señales de estafa.** Por el correo frío entra la mitad de las estafas de
empleo —las denuncias a la FTC pasaron de ~35.000 a más de 105.000 al año entre
2020 y 2024, con pérdidas de 90 a más de 513 millones de dólares— así que un
contacto con señales aparece en el parte pero no te despierta. Ninguna señal
descarta sola y ningún correo se borra: ver `sospechas()`.

Las reglas salen de correos reales del buzón, no de cómo uno se imagina que
escriben: Jobvite, Ashby, Workday, Rippling, Wellfound, applytojob, monstergovt.
"""

import re
from dataclasses import dataclass

# El orden importa: un rechazo casi siempre empieza con "thank you for applying",
# y un acuse puede pedir un video. Gana el estado que más cambia lo que hacés.
#
# `rechazo` va primero porque es el único definitivo: una vez que dijeron que no,
# nada más de ese correo importa. Después `entrevista`, que es lo mejor que puede
# pasar. Después `accion`, que es trabajo tuyo pendiente. `acuse` es el resto.
RECHAZO = re.compile(
    r"\b(not (be )?(moving|move|proceeding|progress)\w* forward"
    r"|(decided|chosen) (to )?not (to )?(move|proceed|continue)"
    r"|move forward with other candidates"
    r"|not (be )?(selected|considered|proceeding)"
    r"|no longer under consideration"
    r"|decision not to move forward"
    r"|we (regret|are unable) to"
    r"|no (vamos a|continuaremos|seguiremos) (avanzar|adelante)"
    r"|not a (good )?(fit|match) (for|at) this time)",
    re.I,
)

ENTREVISTA = re.compile(
    r"\b(schedule (a|an|your) (call|interview|chat|conversation)"
    r"|invite you to (an? )?(interview|call|conversation)"
    r"|would (love|like) to (meet|talk|chat|speak) with you"
    r"|book a time|find a time|pick a time"
    r"|next steps? in (the|our) (process|interview)"
    r"|interview (invitation|request)"
    r"|te (invitamos|queremos conocer)|agendar una (llamada|entrevista))",
    re.I,
)

ACCION = re.compile(
    r"\b(action required|accion requerida|acci[oó]n requerida"
    r"|requires additional information|additional information (is )?(needed|required)"
    r"|(a )?video is required|record (a|your) video"
    # `profile` NO entra: completar el perfil de un portal es alta de usuario,
    # no algo que pida una postulación. Lo destapó un correo de marketing de We
    # Work Remotely —"Complete your profile"— que llegó al teléfono como
    # "acción requerida", que es la alerta que interrumpe siempre.
    r"|complete (your|the) (application|assessment)"
    r"|(take|complete) (the|a|an) (assessment|test|challenge)"
    r"|please (submit|provide|upload|confirm)"
    r"|we need (you to|some)"
    r"|resubmit your application)",
    re.I,
)

ACUSE = re.compile(
    r"\b(thank(s| you) for (taking the time to )?"
    r"(apply|applying|your application|your interest)"
    r"|we('ve| have)? received your application"
    r"|your application (has been|was) (received|submitted)"
    r"|application received|successfully submitted"
    r"|gracias por (postular|tu postulaci[oó]n|aplicar))",
    re.I,
)

# Lo que NO es una postulación aunque el asunto lo parezca. Sin esto, las alertas
# de empleo y los boletines entran como si fueran respuestas de empresas.
NO_ES_RESPUESTA = re.compile(
    r"\b(job alert|new jobs?( that| you)|jobs? for you|we found|recommended for you"
    r"|welcome to job alerts|your job alert|unsubscribe from job"
    r"|newsletter|prequalified|pre-approved|loan|financing"
    r"|working style (results|assessment))",
    re.I,
)


# --- Lo que NO es una respuesta a una postulación tuya -----------------------
#
# Hasta acá el clasificador devolvía "" para todo lo que no reconocía, y el
# lector lo tiraba: ni contado, ni registrado, ni visible. De los sesenta
# correos que se miran por vuelta no había forma de saber cuántos se estaban
# descartando ni de qué eran. Un tipo nuevo de correo —un ATS que cambia la
# plantilla, un reclutador que escribe distinto— desaparecía sin dejar rastro.
#
# Ahora todo correo sale con un estado. Los cuatro de siempre son respuestas a
# algo que mandaste vos; los de abajo son las otras cosas que hay en el buzón.

# Alguien te escribe por un puesto sin que vos hayas postulado. Es la mitad
# buena del correo frío —y también por donde entra la estafa, ver abajo—.
CONTACTO = re.compile(
    r"\b(came across your (profile|résumé|resume|linkedin|github)"
    r"|i came across your|found your (profile|resume|résumé)"
    r"|your (profile|background|experience) (caught|stood out|is a great match)"
    r"|(reaching|reached) out (about|regarding|because)"
    r"|(i am|i'm|we are|we're) (a )?(recruit\w+|hiring|sourcing)"
    r"|would you be (open|interested) (to|in)"
    r"|are you (open|available|interested) (to|in|for)"
    r"|(an )?(exciting|great|new) (opportunity|role|position) (for|at|with)"
    r"|te (escribo|contacto) por (una|la) (vacante|posici[oó]n|oportunidad))",
    re.I,
)

# El reclutador de agencia. No es estafa y no se trata como tal: es otra cosa,
# con otra tasa de éxito, y mezclarlo con el correo directo de una empresa hace
# que el buzón parezca más activo de lo que está.
#
# `corp to corp`, `C2C` y `W2` son la firma del cuerpo de intermediación que
# revende horas en Estados Unidos: no aparecen nunca en un correo de la empresa
# que contrata.
RECLUTADOR_EXTERNO = re.compile(
    r"\b(corp[ -]?to[ -]?corp|\bc2c\b|\bw2\b|1099"
    r"|our client (is|has|are)|one of our clients|my client"
    r"|staffing (agency|solutions|services)|talent (solutions|acquisition partner)"
    r"|(technology|it) (staffing|consulting) (firm|company)"
    r"|submit your (profile|resume) to (our|the) client"
    r"|rate\?|what (is|are) your (hourly |expected )?rates?\b)",
    re.I,
)

# Alertas de empleo y boletines. Ya existía como `NO_ES_RESPUESTA`, que servía
# para DESCARTAR; ahora además es un tipo con nombre, porque "el buzón trajo 31
# alertas" es información y "31 correos que tiré" no lo es.


# --- Señales de estafa -------------------------------------------------------
#
# Ninguna descarta sola, igual que en el criterio de las ofertas: se anotan, se
# muestran, y decidís vos. Lo que sí hacen es sacar al correo de la lista de los
# que interrumpen — un "contacto" con tres señales no te despierta el teléfono.
#
# Los números de la FTC del período 2020-2024: las denuncias por estafa de
# empleo pasaron de ~35.000 a más de 105.000 al año, y las pérdidas declaradas
# de 90 millones de dólares a más de 513 millones. No es un riesgo teórico.

# Proveedores de correo gratis. Un reclutador de una empresa grande escribe
# desde el dominio de la empresa; es la señal más citada de todas, y también la
# que más falsos positivos da sola —un reclutador independiente de verdad usa
# Gmail—. Por eso pesa como una entre varias y no como veredicto.
CORREO_GRATIS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.uk",
        "ymail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "aol.com",
        "protonmail.com",
        "proton.me",
        "gmx.com",
        "gmx.net",
        "mail.com",
        "yandex.com",
        "yandex.ru",
        "mail.ru",
        "icloud.com",
        "me.com",
        "zoho.com",
    }
)

# Plata por adelantado. La regla no tiene excepciones: ningún empleador legítimo
# te cobra por entrenamiento, por equipo ni por el chequeo de antecedentes.
PIDE_PLATA = re.compile(
    r"\b(registration fee|processing fee|application fee|training fee|onboarding fee"
    r"|administrative fee|security deposit"
    r"|(pay|purchase|buy|cover)[^.!?]{0,40}(equipment|laptop|software|starter kit|materials)"
    r"|send (us )?(the )?(money|payment|funds)"
    r"|wire (the )?(money|funds|transfer)"
    r"|(bitcoin|crypto(currency)?|usdt|ethereum|gift cards?)"
    r"|we (will )?(send|mail) you a (check|cheque)"
    r"|deposit (the|this) (check|cheque))",
    re.I,
)

# Mudar la entrevista a un chat. Es la señal más característica de 2026: la
# "entrevista" por Telegram o WhatsApp con un reclutador que nunca muestra la
# cara. Una empresa de verdad agenda por su ATS, por Google Meet o por Zoom.
CHAT_EN_VEZ_DE_ENTREVISTA = re.compile(
    r"\b(telegram|whats ?app|signal app|wechat)\b[^.!?]{0,60}"
    r"\b(interview|chat|conversation|hiring manager|entrevista)"
    r"|\b(interview|entrevista|hiring process)\b[^.!?]{0,60}"
    r"\b(telegram|whats ?app|signal app|wechat)\b"
    r"|\b(add|contact|message|text) (me|us) on (telegram|whats ?app|signal)",
    re.I,
)

# Datos que ninguna empresa pide antes de contratarte.
PIDE_DATOS_SENSIBLES = re.compile(
    r"\b(social security number|\bssn\b|bank (account|details|routing)"
    r"|routing number|void(ed)? (check|cheque)|credit card"
    r"|copy of your (passport|driver'?s? licen[cs]e|id card)"
    r"|n[uú]mero de (cuenta|tarjeta))",
    re.I,
)

# Sueldo grande sin decir qué hay que hacer, y "no se necesita experiencia" en
# un correo dirigido a alguien con diez años de oficio.
PLATA_SIN_TRABAJO = re.compile(
    r"\$\s?\d[\d,.]*\s*(per |/\s?)?(day|week|hour|hr)\b"
    r"|\b(earn|make) (up to )?\$\s?\d"
    r"|\bno experience (is )?(required|needed|necessary)"
    r"|\bweekly (pay|salary) of\b",
    re.I,
)

# Contratado sin hablar con nadie. Una oferta de trabajo que llega antes de la
# primera entrevista no es una buena noticia.
CONTRATADO_SIN_ENTREVISTA = re.compile(
    r"\b(you (have been|'ve been|are) (hired|selected|shortlisted)"
    r"[^.!?]{0,60}(without|no) (interview|screening)"
    r"|congratulations[^.!?]{0,40}(you (have been|were) (hired|selected))"
    r"|immediate (hire|start|employment) (no|without) interview)",
    re.I,
)

# Las señales, y cuáles alcanzan solas para no despertarte.
#
# La división importa y salió de medir contra correos realistas. Una sola señal
# BLANDA no puede silenciar una oportunidad de verdad: un reclutador
# independiente legítimo escribe desde Gmail, y una agencia legítima pone
# "$85/hr" en el asunto. Las DURAS no tienen lectura inocente — ninguna empresa
# te pide plata, ni te entrevista por Telegram, ni te pide el pasaporte antes de
# conocerte.
_SOSPECHAS: tuple[tuple[str, re.Pattern[str], bool], ...] = (
    ("pide plata por adelantado", PIDE_PLATA, True),
    ("la entrevista es por chat", CHAT_EN_VEZ_DE_ENTREVISTA, True),
    ("pide datos bancarios o de identidad", PIDE_DATOS_SENSIBLES, True),
    ("te contrata sin entrevistarte", CONTRATADO_SIN_ENTREVISTA, True),
    ("promete plata sin decir el trabajo", PLATA_SIN_TRABAJO, False),
)

DURAS = {nombre for nombre, _, dura in _SOSPECHAS if dura}
# Las dos que no salen de un patrón de texto sino del SOBRE del correo, y que
# por eso no están en la tabla de arriba. Las dos son duras:
#
# - El `Reply-To` a un correo gratuito que no es el dominio del `From` es el
#   truco de suplantación entero en una línea: el remitente imita a la empresa
#   y tu respuesta se va a otro lado. Un ATS legítimo contesta a otro dominio
#   corporativo, nunca a un Gmail, y eso ya se filtra donde se detecta.
# - Un DMARC fallado en un correo que dice venir de una empresa es la firma de
#   la suplantación, y lo calculó Gmail, no nosotros.
DURAS.add("no pasa la autenticación del remitente")
_DURA_RESPONDER_A = "responder iría a"
DURAS.add(_DURA_RESPONDER_A)
_BLANDA_CORREO_GRATIS = "escribe desde un correo gratuito"


def es_probable_estafa(senales: tuple[str, ...]) -> bool:
    """¿Alcanza lo encontrado para no despertarte con este correo?

    Una señal dura, o dos blandas. Nunca una blanda sola: perder el correo de
    un reclutador de verdad porque escribió desde Gmail cuesta más que ver una
    estafa marcada y decidirlo vos, que es lo que el parte te deja hacer.
    """
    duras = sum(1 for s in senales if any(s.startswith(d) for d in DURAS))
    return duras > 0 or len(senales) >= 2


def _dominio(direccion: str) -> str:
    return direccion.rsplit("@", 1)[-1].strip().strip(">").lower() if "@" in direccion else ""


def sospechas(
    remitente: str, responder_a: str, autenticacion: str, asunto: str, cuerpo: str
) -> tuple[str, ...]:
    """Qué tiene este correo de sospechoso. Vacío no prueba que sea legítimo.

    Son señales del correo mismo, verificables sin salir a la red: el dominio
    del remitente, a dónde te contestaría un "responder", lo que Gmail ya
    verificó de la autenticación, y frases que sólo aparecen en las estafas.

    **Ninguna descarta sola.** Igual que en el criterio de las ofertas, se
    anotan y se muestran. Lo único que hacen es sacar al correo de los que
    interrumpen: un contacto con señales aparece en el parte, no en el teléfono.
    """
    texto = f"{asunto}\n{cuerpo}"
    encontradas = [nombre for nombre, patron, _ in _SOSPECHAS if patron.search(texto)]

    de = _dominio(remitente)
    if de in CORREO_GRATIS:
        # Un reclutador independiente de verdad también usa Gmail. Por eso es
        # una señal y no un veredicto: sola no alcanza para nada.
        encontradas.append(_BLANDA_CORREO_GRATIS)

    hacia = _dominio(responder_a)
    if hacia and de and hacia != de:
        # El truco clásico: el `From` imita a la empresa y el `Reply-To` manda
        # tu respuesta a otro lado. Los ATS legítimos también lo usan, así que
        # sólo se anota cuando el destino es un correo gratuito.
        if hacia in CORREO_GRATIS:
            encontradas.append(f"{_DURA_RESPONDER_A} {hacia}, no a {de}")

    # Gmail ya hizo el trabajo: escribe el resultado de SPF, DKIM y DMARC en la
    # cabecera de cada correo que recibe. Un fallo no prueba fraude —un correo
    # reenviado rompe SPF sin que nadie mienta— pero un DMARC fallado en un
    # correo que dice venir de una empresa grande es la firma de la suplantación.
    minusculas = autenticacion.lower()
    fallados = [p for p in ("dmarc", "dkim", "spf") if f"{p}=fail" in minusculas]
    if fallados:
        encontradas.append(f"no pasa la autenticación del remitente ({', '.join(fallados)})")

    return tuple(encontradas)


# Los estados que son respuesta a algo que mandaste vos. Los usan el
# seguimiento y la medición del retraso: un correo frío de un reclutador no
# prueba que hayas postulado a nada.
DE_POSTULACION = ("rechazo", "entrevista", "accion", "acuse")

# Todos los estados posibles, en el orden en que importan. `otro` es el cajón
# de lo que no se reconoció, y existe para que se pueda MEDIR: sin él, un tipo
# de correo nuevo desaparecía sin dejar rastro.
ESTADOS = (
    "entrevista",
    "accion",
    "contacto",
    "acuse",
    "reclutador",
    "rechazo",
    "alerta",
    "otro",
)

# Los estados que interrumpen. Los demás se cuentan y se guardan.
#
# `contacto` entra —alguien te escribe por un puesto es exactamente lo que
# querés saber hoy— pero sólo si el correo no tiene señales de estafa: ver
# `interrumpe()`. Por ahí entra la mitad de las estafas de empleo, y un canal
# que te despierta con fraude es un canal que dejás de abrir.
AVISABLES = ("entrevista", "accion", "contacto")


@dataclass(frozen=True, slots=True)
class Respuesta:
    """Un correo de una postulación, ya clasificado."""

    id_mensaje: str
    remitente: str
    asunto: str
    fecha: str
    estado: str
    # Qué tiene de sospechoso, si algo. Vacío NO quiere decir legítimo: quiere
    # decir que ninguna de las señales que sabemos mirar se encendió.
    sospechas: tuple[str, ...] = ()

    @property
    def interrumpe(self) -> bool:
        """¿Merece llegar al teléfono ahora?

        Un contacto con señales de estafa no interrumpe: sigue apareciendo en
        `--correos`, con las señales a la vista, pero no te despierta. Los
        estados que sí son respuesta a una postulación tuya interrumpen igual,
        porque ahí ya hay una conversación empezada.
        """
        if self.estado not in AVISABLES:
            return False
        return not (self.estado == "contacto" and es_probable_estafa(self.sospechas))

    @property
    def buzon(self) -> str:
        """La parte local del remitente, en minúsculas.

        Workday manda desde `generalmotors@myworkday.com`: el nombre de la
        empresa está ACÁ y no en el dominio. Medido sobre el buzón, tres de
        cada veinte acuses vienen así, y tirar esta parte los volvía anónimos.
        """
        return self.remitente.rsplit("@", 1)[0].rsplit("<", 1)[-1].strip().lower()

    @property
    def empresa(self) -> str:
        """De qué empresa parece venir, para la línea del aviso.

        Sale del dominio del remitente y no del asunto: los ATS mandan desde
        `no-reply@ashbyhq.com` con el nombre de la empresa sólo en el cuerpo, y
        adivinarlo del asunto acierta a veces y miente el resto.

        Se queda con la etiqueta registrable: se tira la última —que es el
        TLD, sea cual sea— y también `co`/`com` si quedaron al final, que es la
        forma de `example.co.uk`.

        La versión anterior descartaba una lista de cinco TLDs escrita a mano
        (`com, co, io, net, org`) y devolvía el resto. Con eso,
        `daniel@aiscaling.ai` daba **"ai"** y `noreply@notify.nodi.global` daba
        **"global"**: no el nombre de nadie, sino el dominio de primer nivel.
        Los dos casos salieron del buzón de verdad, no de imaginarlos.
        """
        dominio = self.remitente.rsplit("@", 1)[-1].strip(">").lower()
        partes = [p for p in dominio.split(".") if p]
        if len(partes) > 1:
            partes.pop()  # el TLD, cualquiera sea
        if len(partes) > 1 and partes[-1] in ("co", "com"):
            partes.pop()  # `example.co.uk`, ya sin el `uk`
        return partes[-1] if partes else dominio


# Que el correo hable de UNA POSTULACIÓN TUYA. `accion` y `acuse` se detectan
# con frases genéricas —"please submit", "we need you to", "thank you"— que
# cualquier boletín usa, y sin este segundo requisito el marketing de un portal
# de empleo entra como si fuera una respuesta a algo que mandaste.
#
# Es deliberadamente estrecho: "apply" suelto no alcanza, porque el correo que
# destapó esto decía "Save jobs to apply to later" y "not ready to apply".
HABLA_DE_POSTULACION = re.compile(
    r"\b(your application|application (received|submitted|status|for|to)"
    r"|thank(s| you)?( you)? for applying|for applying"
    r"|received your (resume|r[eé]sum[eé]|cv)"
    r"|your (resume|r[eé]sum[eé]|cv) (has been|was) received"
    r"|your candidacy|tu postulaci[oó]n|tu aplicaci[oó]n)",
    re.I,
)

# Los ATS. Si el correo lo manda una plataforma de reclutamiento, habla de una
# postulación tuya POR DEFINICIÓN: a esas direcciones no les escribe nadie más.
#
# Existe porque la compuerta de arriba pide la palabra "application" y hay
# plantillas que nunca la dicen. Medido contra el buzón: de 41 correos reales,
# los dos únicos que caían en `otro` eran acuses de JazzHR —"Thank you for your
# interest in employment at SeedTrust", "Thank you for investing your time to
# explore opportunities with Morning Star"—. Los dos son acuses de libro y los
# dos se perdían.
#
# ⚠ La lista se agranda con lo que aparece en el buzón, no con lo que uno se
# imagina. Las nueve primeras salen de correos reales de estos 14 días; el
# resto son las plataformas grandes que todavía no aparecieron.
ATS_CONOCIDOS = frozenset(
    {
        # vistos en el buzón
        "greenhouse-mail.io",
        "us.greenhouse-mail.io",
        "greenhouse-jobs.com",
        "us.greenhouse-jobs.com",
        "ashbyhq.com",
        "applytojob.com",
        "myworkday.com",
        "jobvite.com",
        "ats.rippling.com",
        "breezy-mail.com",
        "avature.net",
        # las grandes que faltan
        "lever.co",
        "hire.lever.co",
        "icims.com",
        "smartrecruiters.com",
        "workable.com",
        "bamboohr.com",
        "teamtailor.com",
        "recruitee.com",
        "successfactors.com",
        "taleo.net",
        "workablemail.com",
        "greenhouse.io",
    }
)


def de_un_ats(remitente: str) -> bool:
    """Si el correo viene de una plataforma de reclutamiento conocida.

    El dominio se mira por sufijo porque los ATS mandan desde subdominios por
    cliente —`nara-health.breezy-mail.com`, `activision@myworkday.com`— y la
    lista guarda el dominio de la plataforma, no el del cliente.
    """
    dominio = _dominio(remitente)
    if not dominio:
        return False
    return any(dominio == a or dominio.endswith("." + a) for a in ATS_CONOCIDOS)


def clasificar(asunto: str, cuerpo: str, remitente: str = "") -> str:
    """El estado de un correo. Siempre devuelve uno: nada se descarta.

    Antes devolvía `""` para todo lo que no reconocía y el lector lo tiraba sin
    contarlo. De los sesenta correos que se miran por vuelta no había forma de
    saber cuántos caían ahí ni de qué eran, así que un ATS que cambiara la
    plantilla dejaba de verse y nadie se enteraba. Ahora el cajón tiene nombre
    —`otro`— y se puede medir con `--correos`.

    El `remitente` es opcional y sólo se usa para la compuerta de abajo: un
    correo de un ATS habla de una postulación aunque la plantilla nunca diga la
    palabra "application". Sin él, el clasificador se comporta igual que antes.
    """
    texto = f"{asunto}\n{cuerpo}"
    if NO_ES_RESPUESTA.search(texto) and not ACCION.search(texto):
        return "alerta"
    if RECHAZO.search(texto):
        return "rechazo"
    if ENTREVISTA.search(texto):
        return "entrevista"
    # `rechazo` y `entrevista` se dicen de una sola forma y no necesitan esto.
    # Las otras dos salen de frases que cualquiera escribe, así que además
    # tienen que hablar de una postulación tuya.
    if HABLA_DE_POSTULACION.search(texto) or de_un_ats(remitente):
        if ACCION.search(texto):
            return "accion"
        if ACUSE.search(texto):
            return "acuse"
    # No habla de una postulación tuya. Entonces, ¿de qué habla?
    #
    # El reclutador de agencia se mira ANTES que el contacto porque escribe las
    # dos cosas: "vi tu perfil" y "corp to corp". Al revés, todas las agencias
    # entraban como contacto directo y el buzón parecía lleno de empresas
    # escribiéndote.
    if RECLUTADOR_EXTERNO.search(texto):
        return "reclutador"
    if CONTACTO.search(texto):
        return "contacto"
    return "otro"


def inventario(respuestas: list[Respuesta], por_tipo: int = 5) -> str:
    """Qué hay en el buzón, por tipo, sin tirar nada.

    Existe para contestar la pregunta que antes no se podía contestar: de los
    sesenta correos que se miran por vuelta, ¿cuántos se están clasificando y
    cuántos caen en el cajón? Un tipo nuevo —un ATS que cambia la plantilla, un
    reclutador que escribe distinto— aparece acá como un `otro` que crece, y
    eso es lo que permite arreglarlo antes de perder una entrevista.
    """
    if not respuestas:
        return "El buzón no devolvió ningún correo en la ventana mirada."

    por_estado: dict[str, list[Respuesta]] = {}
    for r in respuestas:
        por_estado.setdefault(r.estado, []).append(r)

    etiquetas = {
        "entrevista": "entrevista",
        "accion": "piden algo tuyo",
        "acuse": "acuse de recibo",
        "rechazo": "rechazo",
        "contacto": "te escriben por un puesto",
        "reclutador": "reclutador de agencia",
        "alerta": "alertas y boletines",
        "otro": "sin reconocer",
    }
    lineas = [f"Buzón — {len(respuestas)} correos"]
    for estado in ESTADOS:
        grupo = por_estado.get(estado, [])
        if not grupo:
            continue
        con_senales = sum(1 for r in grupo if r.sospechas)
        aviso = f" · {con_senales} con señales" if con_senales else ""
        lineas.append("")
        lineas.append(f"{etiquetas[estado]} ({len(grupo)}){aviso}")
        # `otro` muestra el doble: es el cajón, y de lo único que sirve es de
        # verlo. Los demás con una muestra alcanza.
        #
        # El resto se cuenta contra lo que de verdad se mostró y no contra
        # `por_tipo`: mostrando diez de doce decía "+7 más", y equivocar ese
        # número justo en el cajón —cuya única función es contar bien lo que no
        # se reconoció— es equivocarlo donde más importa.
        cuantos = por_tipo * 2 if estado == "otro" else por_tipo
        for r in grupo[:cuantos]:
            lineas.append(f"  {r.empresa} — {r.asunto[:70]}")
            for senal in r.sospechas:
                lineas.append(f"    ⚠ {senal}")
        if len(grupo) > cuantos:
            lineas.append(f"  (+{len(grupo) - cuantos} más)")
    return "\n".join(lineas)


def linea(respuesta: Respuesta) -> str:
    """Una línea por pendiente. Sin el cuerpo del correo: es texto de terceros y
    acá sólo sale lo que el código calculó más el asunto, que es lo que te
    permite encontrarlo en el buzón."""
    marca = "entrevista" if respuesta.estado == "entrevista" else "pide algo"
    return f"  [{marca}] {respuesta.empresa} — {respuesta.asunto[:70]}"


def bloque(pendientes: list[Respuesta]) -> str:
    """El pedazo que se agrega al aviso, o `""` si no hay nada que hacer."""
    if not pendientes:
        return ""
    # Las entrevistas primero: es lo único que cambia tu día.
    orden = sorted(pendientes, key=lambda r: 0 if r.estado == "entrevista" else 1)
    return "Tus postulaciones piden algo:\n" + "\n".join(linea(r) for r in orden)
