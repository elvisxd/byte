"""Upwork: pegás la búsqueda, el código dice en cuáles gastar los Connects.

**El problema real de Upwork no es encontrar ofertas, es que postular cuesta
plata.** En el plan Basic son 10 Connects gratis por mes y una propuesta normal
cuesta 6: eso es UNA propuesta al mes, dos si hay suerte. Con ese presupuesto,
"aplicá a todo lo que encaje" no es una estrategia, es quedarse sin Connects el
día 2.

Así que esto no ordena ofertas: reparte un presupuesto. Le pegás el texto de la
búsqueda de Upwork, dice cuántos Connects cuesta cada una, cuáles se llevan el
presupuesto y por qué — y explícitamente cuáles NO, que es la mitad del valor.

**Por qué pegado y no por API.** La API oficial de Upwork no tiene forma de
enviar propuestas y su búsqueda necesita una key aprobada; el raspado está
prohibido y se paga con la cuenta. Copiar la pantalla y pegarla acá lo hace una
persona mirando, que es exactamente lo que Upwork pide. Ver `empleo/README.md`.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from empleo.criterio import Criterio, Puntaje, puntuar
from empleo.oferta import Oferta

# Lo que cuesta una propuesta cuando la oferta no dice otra cosa. Las más
# competidas llegan a 10-16; el costo real lo muestra Upwork al postular.
CONNECTS_POR_DEFECTO = 6

# Cuántas propuestas lleva ya la oferta. Es la señal más fuerte que da la
# pantalla de búsqueda: las primeras propuestas se leen y las demás se hojean.
_PROPUESTAS = re.compile(r"proposals?\s*:?\s*(less than \d+|\d+\s*to\s*\d+|\d+\+?)", re.I)
_VERIFICADO = re.compile(r"payment\s+verified", re.I)
_SIN_VERIFICAR = re.compile(r"payment\s+unverified", re.I)
_GASTADO = re.compile(r"\$([\d.,]+)\s*([KkMm])?\+?\s*spent", re.I)
_CONNECTS = re.compile(r"(\d+)\s*connects?", re.I)
# "Posted 23 minutes ago", "hace 2 horas", "yesterday".
_HACE = re.compile(
    r"(?:posted|publicado)?\s*(?:hace\s*)?(\d+)\s*(minute|minuto|hour|hora|day|d[ií]a|week|semana)",
    re.I,
)
_AYER = re.compile(r"\byesterday|ayer\b", re.I)

_HORAS_POR_UNIDAD = {
    "minute": 1 / 60,
    "minuto": 1 / 60,
    "hour": 1,
    "hora": 1,
    "day": 24,
    "día": 24,
    "dia": 24,
    "week": 168,
    "semana": 168,
}


@dataclass(frozen=True, slots=True)
class OfertaUpwork:
    """Una oferta de la pantalla de búsqueda, con lo que decide si vale gastar.

    Los cuatro datos de abajo no están en `Oferta` porque son de Upwork y de
    ningún otro board: cuántas propuestas lleva, si el cliente tiene el pago
    verificado, cuánto gastó históricamente y cuántos Connects pide.
    """

    oferta: Oferta
    propuestas: int | None = None
    verificado: bool | None = None
    gastado_usd: float | None = None
    connects: int = CONNECTS_POR_DEFECTO
    horas: float | None = None


def _a_horas(bloque: str) -> float | None:
    if _AYER.search(bloque):
        return 24.0
    encontrado = _HACE.search(bloque)
    if not encontrado:
        return None
    unidad = encontrado.group(2).lower()
    return int(encontrado.group(1)) * _HORAS_POR_UNIDAD.get(unidad, 1)


def _propuestas(bloque: str) -> int | None:
    """Cuántas propuestas lleva. Upwork lo da en rangos, así que se toma el ALTO.

    "5 to 10" se lee como 10 y no como 5 a propósito: al repartir un presupuesto
    escaso, equivocarse para el lado optimista es gastar Connects en una oferta
    que ya tenía cola. El pesimismo acá es barato.
    """
    encontrado = _PROPUESTAS.search(bloque)
    if not encontrado:
        return None
    crudo = encontrado.group(1).lower()
    numeros = [int(n) for n in re.findall(r"\d+", crudo)]
    if not numeros:
        return None
    if crudo.startswith("less than"):
        return numeros[0]
    return max(numeros)


def _gastado(bloque: str) -> float | None:
    encontrado = _GASTADO.search(bloque)
    if not encontrado:
        return None
    try:
        cantidad = float(encontrado.group(1).replace(",", ""))
    except ValueError:
        return None
    sufijo = (encontrado.group(2) or "").lower()
    return cantidad * {"k": 1_000, "m": 1_000_000}.get(sufijo, 1)


# El encabezado de cada tarjeta: "Posted 50 minutes ago", "Posted yesterday".
# Es lo único que aparece UNA vez por oferta y siempre al principio, así que es
# lo que marca dónde empieza una y termina la anterior.
_ENCABEZADO = re.compile(r"^\s*(?:posted|publicado)\b.*$", re.I | re.M)

# Renglones de la tarjeta que NO son el título: metadatos, el pie del cliente y
# los encabezados de las listas de skills. Sin esto el título sale siendo
# "Rating is 5.0 out of 5." o "Fixed-price - Expert - Est. Budget: $750".
_NO_ES_TITULO = re.compile(
    r"^\s*(?:proposals?\b|hourly\b|fixed[- ]price\b|est\.?\s|budget\b|skills?\b"
    r"|skip skills\b|payment\s+(?:un)?verified\b|verified\b|unverified\b"
    r"|rating is\b|\$[\d.,]|more\s?about\b|\u2022|\W*$)",
    re.I,
)


def _titulo(lineas: list[str]) -> str:
    """El título de la tarjeta: el primer renglón que no sea metadato.

    En la pantalla de Upwork el título es la primera línea de la tarjeta DESPUÉS
    del "Posted …", y las que le siguen son todas reconocibles como metadato.
    Elegir "el primer renglón largo" —lo que se hacía antes— devolvía la línea
    de presupuesto, que suele ser la más larga de todas.
    """
    for linea in lineas:
        if not _NO_ES_TITULO.match(linea):
            return linea
    return lineas[0] if lineas else ""


def _tarjetas(texto: str) -> list[str]:
    """Parte el pegado en una tarjeta por oferta.

    ⚠ NO se puede cortar por línea en blanco. Upwork deja renglones vacíos
    DENTRO de la tarjeta —antes de "Skills", antes de "Payment verified"—, así
    que cortar ahí parte cada oferta en dos o tres pedazos: el título queda en
    uno y "$300K+ spent" en otro. Medido contra un pegado real: 6 ofertas
    salieron como 12 bloques, y el historial del cliente terminó sumándole
    puntos a un fragmento sin texto.

    El "Posted …" sí aparece una vez por oferta y siempre al principio, así que
    es el corte que sobrevive al maquetado.
    """
    marcas = [m.start() for m in _ENCABEZADO.finditer(texto)]
    if not marcas:
        # Sin encabezados no hay nada mejor que la línea en blanco: es el caso
        # de pegar una sola oferta ya abierta, no la pantalla de búsqueda.
        return [b for b in re.split(r"\n\s*\n", texto.strip()) if b.strip()]
    # Lo que va antes del primer "Posted" es el menú de la página, no una oferta.
    limites = marcas + [len(texto)]
    return [
        texto[i:j].strip() for i, j in zip(limites, limites[1:], strict=False) if texto[i:j].strip()
    ]


def parsear(texto: str) -> list[OfertaUpwork]:
    """Parte el texto pegado en ofertas."""
    salida: list[OfertaUpwork] = []
    for bloque in _tarjetas(texto):
        lineas = [x.strip() for x in bloque.splitlines() if x.strip()]
        if len(lineas) < 2:
            continue

        # La primera línea es el "Posted …" que marcó el corte: el título viene
        # después.
        titulo = _titulo(lineas[1:] if _ENCABEZADO.match(lineas[0]) else lineas)
        horas = _a_horas(bloque)
        connects = _CONNECTS.search(bloque)
        verificado = (
            True
            if _VERIFICADO.search(bloque)
            else (False if _SIN_VERIFICAR.search(bloque) else None)
        )
        salida.append(
            OfertaUpwork(
                oferta=Oferta(
                    fuente="upwork",
                    id_externo="",
                    titulo=titulo[:300],
                    empresa="",
                    url="",
                    descripcion=bloque,
                    # La antigüedad va al campo de siempre para que la puntúe
                    # `criterio.py` con los tramos del TOML. Tener un segundo
                    # bono de frescura acá la contaría dos veces y, peor, con
                    # otros números: el aviso diría "sin fecha" y "hace 20
                    # minutos" en la misma línea.
                    publicada=(
                        (datetime.now(tz=UTC) - timedelta(hours=horas)).isoformat()
                        if horas is not None
                        else ""
                    ),
                ),
                propuestas=_propuestas(bloque),
                verificado=verificado,
                gastado_usd=_gastado(bloque),
                connects=int(connects.group(1)) if connects else CONNECTS_POR_DEFECTO,
                horas=horas,
            )
        )
    return salida


@dataclass(frozen=True, slots=True)
class Veredicto:
    """Una oferta, su puntaje y si conviene gastarle Connects."""

    entrada: OfertaUpwork
    puntaje: Puntaje
    ajuste: int
    motivos: tuple[str, ...]

    @property
    def total(self) -> int:
        return self.puntaje.total + self.ajuste


# Lo que suma o resta de la pantalla de Upwork, y por qué.
#
# Sólo lo que es propio de Upwork y no existe en los demás boards. La frescura
# NO está acá: va al campo `publicada` de la oferta y la puntúa `criterio.py`
# con los mismos tramos que todo lo demás.
#
# Las propuestas pesan más que nada: con las primeras el cliente lee y con las
# siguientes hojea. Los números que circulan —"las 5 primeras reciben 3 a 5
# veces más vistas"— salen de blogs de empresas que venden herramientas de
# bidding, así que se usa la dirección, que es sólida, y no la magnitud.
def _ajustar(entrada: OfertaUpwork) -> tuple[int, list[str]]:
    ajuste = 0
    motivos: list[str] = []

    if entrada.propuestas is not None:
        if entrada.propuestas <= 5:
            ajuste += 30
            motivos.append(f"+30 solo {entrada.propuestas} propuestas")
        elif entrada.propuestas <= 10:
            ajuste += 10
            motivos.append(f"+10 {entrada.propuestas} propuestas")
        elif entrada.propuestas <= 20:
            motivos.append(f"±0 {entrada.propuestas} propuestas")
        else:
            ajuste -= 25
            motivos.append(f"-25 ya hay {entrada.propuestas}+ propuestas")

    # Un cliente sin pago verificado puede no llegar a contratar nunca, y la
    # propuesta ya se pagó. Con Connects gratis escasos, eso importa.
    if entrada.verificado is False:
        ajuste -= 30
        motivos.append("-30 pago sin verificar")
    elif entrada.verificado is True:
        ajuste += 10
        motivos.append("+10 pago verificado")

    # El historial del cliente, por tramos y no todo-o-nada.
    #
    # Lo que separa al que contrata del que publica y desaparece no es un umbral
    # sino una escala: $300K gastados y $5K son los dos "cliente con historial",
    # pero no son el mismo cliente. Y el que gastó $0 no es neutro —es el que
    # todavía no contrató a nadie—, así que resta: con Connects escasos, una
    # propuesta a alguien que puede no contratar nunca es la más cara de todas.
    #
    # ⚠ $0 y "sin dato" son cosas distintas. Upwork muestra "$0 spent" para el
    # cliente nuevo y no muestra nada cuando el pegado se cortó; castigar el
    # segundo caso convertiría un copiado incompleto en un cliente malo.
    if entrada.gastado_usd is not None:
        gastado = entrada.gastado_usd
        if gastado >= 100_000:
            ajuste += 30
            motivos.append(f"+30 cliente con ${gastado:,.0f} gastados")
        elif gastado >= 10_000:
            ajuste += 20
            motivos.append(f"+20 cliente con ${gastado:,.0f} gastados")
        elif gastado >= 1_000:
            ajuste += 10
            motivos.append(f"+10 cliente con ${gastado:,.0f} gastados")
        elif gastado > 0:
            motivos.append(f"±0 cliente con ${gastado:,.0f} gastados")
        else:
            ajuste -= 15
            motivos.append("-15 cliente que nunca contrató ($0)")

    return ajuste, motivos


def evaluar(entradas: list[OfertaUpwork], criterio: Criterio) -> list[Veredicto]:
    """Puntúa cada oferta y las ordena de mejor a peor."""
    veredictos = []
    for entrada in entradas:
        ajuste, motivos = _ajustar(entrada)
        veredictos.append(
            Veredicto(entrada, puntuar(entrada.oferta, criterio), ajuste, tuple(motivos))
        )
    veredictos.sort(key=lambda v: v.total, reverse=True)
    return veredictos


def repartir(veredictos: list[Veredicto], presupuesto: int, minimo: int) -> list[Veredicto]:
    """Cuáles se llevan los Connects, en orden, hasta que se acaben.

    Se gasta de la mejor hacia abajo y se corta al llegar al mínimo: con uno o
    dos tiros por mes, guardarse los Connects para la semana que viene suele ser
    mejor que gastarlos en la tercera mejor de hoy.
    """
    elegidas: list[Veredicto] = []
    queda = presupuesto
    for veredicto in veredictos:
        if veredicto.total < minimo:
            break
        if veredicto.entrada.connects <= queda:
            elegidas.append(veredicto)
            queda -= veredicto.entrada.connects
    return elegidas


# --- CLI ---


def _linea(veredicto: Veredicto, gastar: bool) -> str:
    entrada = veredicto.entrada
    marca = f"GASTAR {entrada.connects} connects" if gastar else "no"
    datos = []
    if entrada.propuestas is not None:
        datos.append(f"{entrada.propuestas} propuestas")
    if entrada.horas is not None:
        datos.append(
            f"hace {entrada.horas:.0f} h"
            if entrada.horas < 48
            else f"hace {entrada.horas / 24:.0f} d"
        )
    if entrada.verificado is False:
        datos.append("SIN verificar")
    motivos = "; ".join(veredicto.puntaje.motivos + veredicto.motivos)
    return (
        f"[{marca}]  {veredicto.total:>4}  {entrada.oferta.titulo[:70]}\n"
        f"        {' · '.join(datos) or 'sin datos de la pantalla'}\n"
        f"        {motivos}"
    )


def informe(texto: str, criterio: Criterio, presupuesto: int) -> str:
    """Lo que se imprime: qué gastar, qué no, y cuánto queda."""
    entradas = parsear(texto)
    if not entradas:
        return (
            "No reconocí ninguna oferta en lo pegado.\n"
            "Copiá las tarjetas de la búsqueda de Upwork tal cual, con el "
            "renglón en blanco entre una y otra."
        )

    veredictos = evaluar(entradas, criterio)
    elegidas = repartir(veredictos, presupuesto, criterio.puntaje_minimo)
    ids = {id(v) for v in elegidas}
    gastado = sum(v.entrada.connects for v in elegidas)

    lineas = [
        f"{len(entradas)} ofertas · presupuesto {presupuesto} connects "
        f"· mínimo para gastar: {criterio.puntaje_minimo} puntos",
        "",
    ]
    lineas += [_linea(v, id(v) in ids) for v in veredictos]
    lineas += [
        "",
        f"Gastarías {gastado} de {presupuesto} connects en {len(elegidas)} "
        f"propuesta(s); quedan {presupuesto - gastado}.",
    ]
    if not elegidas:
        lineas.append(
            "Ninguna llegó al mínimo. Con uno o dos tiros por mes, no gastar "
            "hoy es una decisión, no una falla."
        )
    return "\n".join(lineas)


def main() -> None:
    import argparse
    import sys
    from pathlib import Path

    from empleo.criterio import cargar_criterio

    raiz = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Pegá la búsqueda de Upwork por la entrada estándar y decide dónde gastar."
    )
    parser.add_argument(
        "--connects",
        type=int,
        default=10,
        help="Connects disponibles. 10 es lo que da el plan Basic por mes.",
    )
    parser.add_argument("--criterio", type=Path, default=raiz / "perfil" / "busqueda.toml")
    args = parser.parse_args()

    texto = sys.stdin.read()
    print(informe(texto, cargar_criterio(args.criterio), args.connects))


if __name__ == "__main__":
    main()
