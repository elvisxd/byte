"""En qué quedó cada postulación, leído de tu propio buzón.

Postularse es la mitad barata. La cara es **no perder** lo que ya postulaste: un
"falta el video" que se lee once días tarde es una postulación regalada, y un
rechazo que no registrás es una empresa a la que volvés a escribir.

Esto lee el correo por IMAP —el mismo camino que ya usa la fuente de LinkedIn,
con la misma contraseña de aplicación y en **solo lectura**: no marca, no mueve
ni borra nada— y clasifica cada mensaje por reglas. No lo decide un modelo: las
frases son fijas, las escribió gente que usa plantillas, y una clasificación que
cambia sola entre corridas no se puede revisar.

**Sólo dos estados llegan al teléfono: los que piden algo tuyo.** Los rechazos y
los acuses se cuentan y se guardan, pero no interrumpen. Cinco notificaciones de
"gracias por postularte" por semana son la forma más rápida de que dejes de
abrir el canal — el mismo error que ya cometimos con el cazador y arreglamos.

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
    r"|complete (your|the) (application|profile|assessment)"
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

# Los estados que interrumpen. Los otros dos se cuentan y se guardan.
AVISABLES = ("entrevista", "accion")


@dataclass(frozen=True, slots=True)
class Respuesta:
    """Un correo de una postulación, ya clasificado."""

    id_mensaje: str
    remitente: str
    asunto: str
    fecha: str
    estado: str

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


def clasificar(asunto: str, cuerpo: str) -> str:
    """El estado de un correo. `""` si no parece una respuesta de postulación."""
    texto = f"{asunto}\n{cuerpo}"
    if NO_ES_RESPUESTA.search(texto) and not ACCION.search(texto):
        return ""
    if RECHAZO.search(texto):
        return "rechazo"
    if ENTREVISTA.search(texto):
        return "entrevista"
    if ACCION.search(texto):
        return "accion"
    if ACUSE.search(texto):
        return "acuse"
    return ""


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
