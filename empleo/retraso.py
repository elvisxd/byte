"""Cuánto tardás en postular después de que el cazador te avisa.

El criterio le da 25 puntos a una oferta de menos de 24 horas y 8 a una de más
de 48. Ese peso existe por una tesis concreta —el reclutador lee las primeras
20 o 40 postulaciones de la cola, no las 300—, y esa tesis es sobre **cuándo
postulás vos**, no sobre cuándo la vio un cron.

Todo lo que el sistema mide termina en "te lo dije". El `+25` es una apuesta a
que el hueco entre "te lo dije" y "postulaste" es chico, y ese hueco no se
medía nunca. Si la mediana son tres días, la frescura es decoración: ordena las
ofertas por lo temprano que las vio el cazador, no por lo temprano que entrás
vos en la cola.

Los dos extremos ya estaban guardados, en dos lugares que no se hablaban:

- **Los digests** del disco: un archivo por vuelta, con la fecha en el nombre y
  cada oferta con su puntaje, su empresa y su link.
- **El acuse de recibo** en el buzón. `postulaciones.py` ya clasifica los
  "gracias por postularte", y ese correo llega minutos después de que apretás
  Enviar: su fecha es, con buena aproximación, cuándo postulaste.

Esto los resta. **No cambia ningún peso**: primero se mide, después se decide,
que es como se justificó cada número del TOML.
"""

import re
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from empleo.postulaciones import Respuesta

# El nombre del archivo que escribe `escribir_digest`: 2026-09-19-1430.md
_NOMBRE_DIGEST = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})\.md$")
# La cabecera de cada oferta adentro: "## 40 · Senior AI Engineer — Cohere".
_ENCABEZADO = re.compile(r"^## (-?\d+) · (.+)$")
_ENLACE = re.compile(r"^- (https?://\S+)$")
# Lo que `escribir_digest` pone entre el título y la empresa. Sólo cuando hay
# empresa: hay feeds que no la mandan.
SEPARADOR_EMPRESA = " — "
_NO_ALFANUM = re.compile(r"[^a-z0-9]+")

# Dominios de ATS: mandan en nombre de la empresa pero no la nombran. Un acuse
# de `no-reply@ashbyhq.com` no se puede atar a ninguna oferta, y contarlo como
# "no emparejada" sin decir por qué haría parecer que el emparejado falla más
# de lo que falla.
INTERMEDIARIOS = frozenset(
    {
        "ashbyhq",
        "greenhouse",
        "greenhouse-mail",
        "lever",
        "myworkday",
        "workday",
        "jobvite",
        "rippling",
        "wellfound",
        "applytojob",
        "smartrecruiters",
        "workable",
        "icims",
        "taleo",
        "successfactors",
        "bamboohr",
        "hire",
        "notifications",
    }
)

# Qué estados cuentan como "postuló". El acuse es el que sirve: llega solo y
# enseguida. Un rechazo o una entrevista también prueban que postulaste, pero
# llegan días o semanas después y medirían otra cosa.
PRUEBA_DE_POSTULACION = ("acuse", "accion")

# Debajo de esto se muestra la mediana sola. Un p25 y un p75 sobre tres datos
# son los propios datos con otro nombre, y se leen como si midieran algo.
MINIMO_PARA_PERCENTILES = 4


@dataclass(frozen=True, slots=True)
class Aviso:
    """Una oferta tal como quedó anotada en un digest, con cuándo se anotó."""

    cuando: datetime
    puntaje: int
    titulo: str
    empresa: str
    url: str


def _normalizar(texto: str) -> str:
    return _NO_ALFANUM.sub(" ", texto.lower()).strip()


def leer_avisos(carpeta: Path) -> list[Aviso]:
    """Todas las ofertas anotadas en los digests, la más nueva primero.

    La fecha sale del nombre del archivo —lo escribe `escribir_digest` con la
    hora local de la máquina que corrió la vuelta— y se le pega esa misma zona
    al leerlo. Si el digest se escribió en Railway y se lee en otra máquina con
    otro huso, el corrimiento es de horas; la mediana que sale de acá se mide en
    horas y días, así que no cambia la conclusión, pero conviene saberlo.
    """
    if not carpeta.is_dir():
        return []

    avisos: list[Aviso] = []
    vistos: set[str] = set()
    for archivo in sorted(carpeta.glob("*.md"), reverse=True):
        nombre = _NOMBRE_DIGEST.match(archivo.name)
        if not nombre:
            continue
        dia, hora, minuto = nombre.groups()
        cuando = datetime.fromisoformat(f"{dia}T{hora}:{minuto}").astimezone()

        pendiente: tuple[int, str, str] | None = None
        for linea in archivo.read_text(encoding="utf-8", errors="replace").splitlines():
            encabezado = _ENCABEZADO.match(linea)
            if encabezado:
                puntaje, resto = encabezado.groups()
                # El título puede llevar guiones; la empresa va al final y sin
                # ellos, así que se corta por el ÚLTIMO separador.
                #
                # Y hay ofertas SIN empresa: `escribir_digest` omite el " — "
                # cuando el feed no la manda, y Get on Board no la manda nunca
                # —ver `test_la_empresa_de_getonbrd_queda_vacia_en_vez_de_adivinada`—.
                # Con `rpartition` ahí el título terminaba también en la empresa,
                # y el informe mostraba "X — X". Peor que feo: el emparejado
                # busca el dominio del remitente adentro del nombre de la
                # empresa, y con el título entero ahí adentro ataría acuses a
                # ofertas que no son.
                if SEPARADOR_EMPRESA in resto:
                    titulo, empresa = resto.rsplit(SEPARADOR_EMPRESA, 1)
                else:
                    titulo, empresa = resto, ""
                pendiente = (int(puntaje), titulo, empresa)
                continue
            enlace = _ENLACE.match(linea)
            if enlace and pendiente is not None:
                url = enlace.group(1)
                if url not in vistos:
                    vistos.add(url)
                    avisos.append(Aviso(cuando, pendiente[0], pendiente[1], pendiente[2], url))
                pendiente = None
    return avisos


def _fecha_de(respuesta: Respuesta) -> datetime | None:
    """La fecha del correo, en UTC. `None` antes que adivinar."""
    try:
        return parsedate_to_datetime(respuesta.fecha).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def emparejar(
    avisos: list[Aviso], respuestas: list[Respuesta]
) -> tuple[list[tuple[Aviso, Respuesta, float]], list[Respuesta], list[Respuesta]]:
    """Ata cada acuse a la oferta que lo originó. Devuelve (atados, por ATS, sueltos).

    El emparejado es por empresa y no por link: el correo de respuesta nunca
    trae el link de la oferta. `Respuesta.empresa` sale del dominio del
    remitente —`cohere.com` da `cohere`— y se busca esa palabra adentro del
    nombre de la empresa del digest.

    Es parcial a propósito, y por eso las tres listas: lo que llega desde un
    ATS no se puede atar a nada, y mezclarlo con los fallos reales del
    emparejado haría creer que esto acierta menos de lo que acierta.
    """
    atados: list[tuple[Aviso, Respuesta, float]] = []
    por_ats: list[Respuesta] = []
    sueltos: list[Respuesta] = []

    for respuesta in respuestas:
        if respuesta.estado not in PRUEBA_DE_POSTULACION:
            continue
        cuando = _fecha_de(respuesta)
        if cuando is None:
            sueltos.append(respuesta)
            continue
        if respuesta.empresa in INTERMEDIARIOS:
            por_ats.append(respuesta)
            continue

        clave = respuesta.empresa
        # El aviso más reciente ANTERIOR al correo: postulaste a lo que te
        # avisaron, no a lo que te avisarían después.
        candidatos = [
            a
            for a in avisos
            if a.cuando <= cuando
            and clave
            and a.empresa
            and clave in _normalizar(a.empresa).replace(" ", "")
        ]
        if not candidatos:
            sueltos.append(respuesta)
            continue
        aviso = max(candidatos, key=lambda a: a.cuando)
        atados.append((aviso, respuesta, (cuando - aviso.cuando).total_seconds() / 3600))

    return atados, por_ats, sueltos


def _duracion(horas: float) -> str:
    if horas < 48:
        return f"{horas:.0f} h"
    return f"{horas / 24:.1f} d"


def _percentil(valores: list[float], fraccion: float) -> float:
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, int(fraccion * len(ordenados)))
    return ordenados[indice]


@dataclass(frozen=True, slots=True)
class Medicion:
    """Lo que salió de restar los digests contra el buzón, ya contado."""

    atados: list[tuple[Aviso, Respuesta, float]]
    por_ats: list[Respuesta]
    sueltos: list[Respuesta]
    huerfanas: list[Aviso]
    a_tiempo: list[Aviso]
    respuestas: int
    sin_buzon: str


def medir(
    avisos: list[Aviso],
    respuestas: list[Respuesta],
    puntaje_minimo: int,
    horas_a_tiempo: float,
    sin_buzon: str = "",
) -> Medicion:
    """Las cuentas, una sola vez. El informe largo y el resumen las comparten."""
    atados, por_ats, sueltos = emparejar(avisos, respuestas)
    postuladas = {aviso.url for aviso, _, _ in atados}
    ahora = datetime.now(tz=UTC)
    huerfanas = [a for a in avisos if a.puntaje >= puntaje_minimo and a.url not in postuladas]
    return Medicion(
        atados=atados,
        por_ats=por_ats,
        sueltos=sueltos,
        huerfanas=huerfanas,
        a_tiempo=[
            a for a in huerfanas if (ahora - a.cuando).total_seconds() / 3600 <= horas_a_tiempo
        ],
        respuestas=len(respuestas),
        sin_buzon=sin_buzon,
    )


def resumen(medicion: Medicion) -> str:
    """El informe en cuatro renglones, para el teléfono.

    El panel rechaza los textos de más de 1000 caracteres, y el informe largo
    los pasa apenas hay unas pocas ofertas sin postular. Acá van los números y
    no la lista: los links se miran en el digest, sentado.
    """
    if medicion.sin_buzon:
        cabeza = f"sin buzón ({medicion.sin_buzon}): no hay retraso que medir"
    elif medicion.atados:
        horas = [h for _, _, h in medicion.atados]
        cabeza = (
            f"mediana {_duracion(statistics.median(horas))}"
            f" · {len(medicion.atados)} de {medicion.respuestas} acuses atados"
        )
    else:
        cabeza = f"ningún acuse atado, de {medicion.respuestas}"

    return (
        f"Retraso — {datetime.now().strftime('%d/%m')}\n"
        f"{cabeza}\n"
        f"avisadas sin postular: {len(medicion.huerfanas)}"
        f" · a tiempo: {len(medicion.a_tiempo)}"
    )


def informe(medicion: Medicion) -> str:
    """El parte largo: cuánto tardás, y qué te avisaron que nunca postulaste.

    `Medicion.sin_buzon` dice por qué no se pudo leer el correo, si no se pudo.
    Importa que sea explícito: sin buzón **todas** las ofertas figuran sin
    postular, y ese número se lee como "ignoraste 121 ofertas" cuando en
    realidad es "no miramos". Un informe que no distingue las dos cosas es el
    mismo fallo silencioso que este comando existe para destapar.
    """
    if not medicion.huerfanas and not medicion.atados and not medicion.respuestas:
        if not medicion.sin_buzon:
            return (
                "No hay digests en la carpeta de trabajo, así que no hay con qué medir.\n"
                "Los escribe el cazador en cada vuelta; en Railway viven en el volumen."
            )

    lineas = ["Retraso entre «te avisé» y «postulaste»", ""]

    if medicion.sin_buzon:
        lineas += [
            f"  NO SE LEYÓ EL BUZÓN: {medicion.sin_buzon}",
            "  Sin él no hay retraso que medir, y lo de abajo no está comprobado.",
        ]
    elif not medicion.respuestas:
        lineas.append("  el buzón no trajo ninguna respuesta de postulación en el período.")
    elif medicion.atados:
        horas = [h for _, _, h in medicion.atados]
        lineas.append(f"  emparejadas: {len(medicion.atados)} de {medicion.respuestas} acuses")
        # Los percentiles sobre dos o tres datos son ruido con cara de
        # estadística: con tan poco, la mediana sola ya dice lo que hay.
        if len(horas) >= MINIMO_PARA_PERCENTILES:
            lineas.append(
                f"  mediana: {_duracion(statistics.median(horas))}"
                f"   ·  p25: {_duracion(_percentil(horas, 0.25))}"
                f"   ·  p75: {_duracion(_percentil(horas, 0.75))}"
            )
        else:
            lineas.append(
                f"  mediana: {_duracion(statistics.median(horas))}"
                f"   (pocos datos todavía: {len(horas)})"
            )
    else:
        lineas.append(
            f"  de {medicion.respuestas} respuestas del buzón, ningún acuse se pudo "
            "atar a una oferta avisada."
        )
    if medicion.por_ats:
        llego = "llegó" if len(medicion.por_ats) == 1 else "llegaron"
        lineas.append(
            f"  {len(medicion.por_ats)} {llego} desde un ATS (ashbyhq, greenhouse…): "
            "el correo no nombra a la empresa y no se puede atar."
        )
    if medicion.sueltos:
        lineas.append(f"  {len(medicion.sueltos)} sin oferta previa que les corresponda.")

    rastro = "sin comprobar contra el buzón" if medicion.sin_buzon else "sin rastro de postulación"
    lineas += ["", f"Avisadas, {rastro}: {len(medicion.huerfanas)}"]

    ahora = datetime.now(tz=UTC)
    if medicion.a_tiempo:
        lineas.append(f"  todavía a tiempo: {len(medicion.a_tiempo)}")
        for aviso in sorted(medicion.a_tiempo, key=lambda a: a.puntaje, reverse=True)[:8]:
            hace = (ahora - aviso.cuando).total_seconds() / 3600
            empresa = f" — {aviso.empresa}" if aviso.empresa else ""
            lineas.append(
                f"    {aviso.puntaje:>4} · hace {_duracion(hace):>6}  {aviso.titulo[:52]}{empresa}"
            )
            lineas.append(f"           {aviso.url}")
    elif medicion.huerfanas:
        lineas.append("  ninguna sigue a tiempo.")

    lineas += [
        "",
        "Un acuse que no llega no prueba que no postulaste: hay empresas que no",
        "mandan ninguno. Esto mide lo que se puede ver desde el buzón.",
    ]
    if medicion.sin_buzon:
        lineas.append("Y esta vez ni eso: falta la credencial, así que la lista de arriba")
        lineas.append("es todo lo avisado, no lo que quedó sin postular.")
    return "\n".join(lineas)
