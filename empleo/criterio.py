"""Qué tan cerca está una oferta de lo que buscás, y por qué.

**El puntaje lo calcula el código, nunca el modelo.** Es la misma regla que
gobierna `paper/`: el modelo sirve para explicar y redactar, no para decir un
número. Un 7B pidiéndole "puntuá esta oferta del 1 al 100" devuelve algo
plausible y distinto cada vez; acá cada punto tiene una línea que lo justifica y
se puede discutir mirando el archivo `perfil/busqueda.toml`.

**Ninguna señal descarta sola.** Una oferta que dice "US only" o "no visa
sponsorship" baja de puesto, pero aparece con la señal a la vista. Filtrar en
silencio es cómo un criterio equivocado se vuelve invisible: no verías las
ofertas que te estás perdiendo, verías menos ofertas y nada más.
"""

import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from empleo.oferta import Oferta

# `logging` de la biblioteca estándar y no `api.logging`: este módulo lo usa
# también `ofertas-service`, cuya imagen instala tres paquetes y no trae `api/`.
# Importar de `api` acá rompería ese servicio al arrancar.
logger = logging.getLogger(__name__)

# Lo que se busca en el texto de la oferta. Cada patrón se probó contra la forma
# en que estas frases aparecen de verdad, no contra la forma "correcta": nadie
# escribe "visa sponsorship is not available", escriben "we can't sponsor".
# Frases que dicen explícitamente que NO hay oficina. Si alguna aparece, las
# señales `hibrido` y `presencial` se descartan aunque sus palabras estén en el
# texto: "100% remote, no hybrid" las contiene a las dos y significa lo opuesto.
NIEGA_OFICINA = re.compile(
    r"\b((no|sin|not)\s*(a\s*)?(hybrid|h[ií]brido|on[ -]?site|in[ -]?office|office|oficina)"
    r"|fully remote|100% remote|remote[ -]first|totalmente remoto|remoto total)\b"
)

# Frases que dicen que NO hay reubicación. Mismo problema que la oficina, y peor
# consecuencia: "no relocation assistance is provided" contiene todas las
# palabras de una buena noticia y significa exactamente lo contrario. Sin esto,
# una oferta que te avisa que te mudás por tu cuenta sumaba como si te pagaran
# el pasaje.
NIEGA_REUBICACION = re.compile(
    r"\b(no|not|without|sin|nunca)\b[^.!?]{0,30}\brelocat\w*"
    r"|\brelocat\w*[^.!?]{0,30}\b(not|no longer|unavailable|isn't|is not)\b"
)

SENALES: dict[str, re.Pattern[str]] = {
    "latam": re.compile(
        r"\b(lat(?:in)?[ -]?am(?:erica)?n?|south america|central america|the americas"
        r"|argentina|colombia|mexico|m[eé]xico|brazil|brasil|venezuela|chile|per[uú]"
        r"|uruguay|ecuador|costa rica)\b"
    ),
    "remoto_global": re.compile(
        r"\b(anywhere in the world|work from anywhere|worldwide|globally distributed"
        r"|fully distributed|any time ?zone|100% remote, anywhere)\b"
    ),
    # Reubicación. El patrón viejo pedía casi la frase exacta y se perdía la
    # mitad de las formas reales de decirlo: "we offer relocation", "includes
    # relocation", "we sponsor visas and pay for relocation". Medido sobre doce
    # frases sacadas de ofertas, seis no se detectaban.
    "reubicacion": re.compile(
        r"\b(relocat\w*[ ,:-]{0,3}(package|assistance|support|bonus|allowance|stipend"
        r"|provided|offered|included|available|paid|covered|yes)"
        r"|(offer|provide|includ|cover|pay|fund|sponsor|assist|help|handl)\w*"
        r"[^.!?]{0,30}\brelocat\w*"
        r"|full relocation|relocation and visa|visa[^.!?]{0,30}relocat\w*)"
    ),
    "contractor": re.compile(
        r"\b(contractor|c2c|corp[ -]to[ -]corp|employer of record|eor|1099|deel"
        r"|remote\.com|independent contractor)\b"
    ),
    "freelance": re.compile(r"\b(freelance|per[ -]project|hourly rate|short[ -]term contract)\b"),
    # Híbrido y presencial: el caso que originó esto es una oferta de Santiago
    # marcada "híbrida" a la que no se puede aplicar desde Estados Unidos. No
    # es un puesto peor, es un puesto imposible, y encima los boards de la
    # región están llenos.
    #
    # Se busca la negación PRIMERO y gana: "fully remote, no hybrid" y "no
    # on-site requirement" dicen lo contrario de lo que sus palabras sugieren,
    # y son frases comunes justo en las ofertas que sí sirven.
    "hibrido": re.compile(
        r"\b(h[ií]brid[oa]|hybrid|"
        r"\d+\s*(days?|d[ií]as?)\s*(a|per|por)\s*(week|semana)\s*(in|at|en)\s*"
        r"(the\s*)?(office|oficina)|"
        r"(some|algunos)\s*(days?|d[ií]as?)\s*(in|at|en)\s*(the\s*)?(office|oficina))\b"
    ),
    "presencial": re.compile(
        r"\b(presencial|on[ -]?site|in[ -]?office|in[ -]?person|"
        r"work from (our|the) office|desde (la|nuestra) oficina)\b"
    ),
    "junior": re.compile(
        r"\b(junior|jr\.?|entry[ -]level|intern(ship)?|new grad|graduate program"
        r"|0[ -–-]2 years|1[ -–-]2 years)\b"
    ),
    # El orden importa: `sin_patrocinio` se evalúa antes que `patrocinio`, porque
    # "we do not offer visa sponsorship" contiene la frase positiva adentro.
    "sin_patrocinio": re.compile(
        r"\b((can ?not|cannot|can't|do not|don't|unable to|not able to|won't|will not)"
        r"\s+(currently\s+)?(provide|offer|support)?\s*(visa\s+)?sponsor\w*"
        r"|no (visa )?sponsorship|without sponsorship|sponsorship is not"
        # "Tenés que ser residente permanente" es un NO a sponsorizar, dicho como
        # requisito. Sin esto, "permanent resident" en una oferta canadiense se
        # confundía con la buena noticia de abajo.
        r"|must (be|have|hold) (a |an )?(canadian |u\.?s\.? |american )?"
        r"(citizen|permanent resident)"
        r"|only (canadian |u\.?s\.? )?(citizens|permanent residents)"
        r"|permanent residen\w* (is )?(required|mandatory))"
    ),
    # Patrocinio. Las frases de Canadá son otras y ninguna estaba: medido sobre
    # nueve formas reales, siete no se detectaban. LMIA es el instrumento con el
    # que un empleador canadiense contrata a alguien de afuera, y Global Talent
    # Stream es la vía rápida para puestos de tecnología.
    "patrocinio": re.compile(
        r"\b(visa sponsorship|we sponsor|sponsorship (is )?(available|provided|offered)"
        r"|h-?1b|green card|work permit"
        r"|lmia|global talent stream"
        r"|open to (international|foreign) (candidates|applicants)"
        r"|immigration (support|assistance|sponsorship)"
        r"|(path|pathway) to (permanent residency|pr)"
        r"|we (will )?(support|sponsor|handle|cover)[^.!?]{0,25}"
        r"(work permit|visa|immigration|relocation to))\b"
    ),
    "solo_us": re.compile(
        r"\b(u\.?s\.?a?[ -]only|united states only|us[ -]based (only|candidates)"
        r"|must (be |reside |live ).{0,30}(united states|u\.?s\.?a?\b)"
        r"|authorized to work in the (us|u\.?s\.?a?|united states))\b"
    ),
}


@dataclass(frozen=True, slots=True)
class Criterio:
    """Lo que dice `perfil/busqueda.toml`, ya validado."""

    stack: dict[str, tuple[str, ...]] = field(default_factory=dict)
    pesos_stack: dict[str, int] = field(default_factory=dict)
    preferencias: dict[str, int] = field(default_factory=dict)
    penalizaciones: dict[str, int] = field(default_factory=dict)
    necesita_patrocinio: bool = False
    # Países donde un puesto presencial o híbrido SÍ sirve. Vacío = ninguno.
    presencial_aceptable_en: tuple[str, ...] = ()
    fuentes: dict[str, bool] = field(default_factory=dict)
    tope_por_aviso: int = 8
    puntaje_minimo: int = 25
    # Cuántas ofertas de la MISMA empresa entran en un aviso. Los marketplaces
    # de talento —Lemon.io, Toptal y parecidos— republican su catálogo entero
    # todo el tiempo: sin esto, una sola empresa se lleva cinco de los ocho
    # lugares y las ofertas frescas del resto no llegan al teléfono. Las que
    # sobran no se pierden, quedan en el digest.
    tope_por_empresa: int = 2
    # Días a partir de los cuales una oferta no entra al aviso, por buena que
    # sea. Restar puntos no alcanza: una oferta con el stack entero absorbe la
    # penalización de frescura y sigue arriba, y a las cuatro semanas ya
    # entrevistaron a alguien. Las viejas siguen en el digest. 0 lo apaga.
    descartar_despues_de_dias: int = 0
    # Cuántas horas de silencio se aguantan antes de mandar una línea de
    # "sigo vivo". Sin esto, "no había nada" y "el cron está muerto" se ven
    # exactamente igual desde el teléfono: los dos son no recibir nada. 0 lo
    # apaga y el silencio pasa a ser total.
    horas_sin_aviso: int = 24
    # Cuánto puede aportar el stack como máximo. Sin tope, una oferta que lista
    # treinta tecnologías en un párrafo de "nice to have" le gana a una que pide
    # exactamente lo que hacés.
    tope_stack: int = 60
    frescura: dict[str, int] = field(default_factory=dict)
    # `(nombre, ats, token)` por empresa, del `[[empresas]]` del TOML.
    empresas: tuple[tuple[str, str, str], ...] = ()


# Cuánto vale encontrar un término de cada grupo.
PESOS = {"fuerte": 12, "medio": 6, "leve": 2}

DEFECTOS_PREFERENCIAS = {
    "latam": 30,
    "remoto_global": 20,
    "reubicacion": 20,
    "patrocinio": 15,
    "contractor": 12,
    "freelance": 5,
}
DEFECTOS_PENALIZACIONES = {
    "junior": 40,
    "sin_patrocinio": 25,
    "solo_us": 0,
    "hibrido": 45,
    "presencial": 45,
}

# Cuánto vale llegar temprano. Los tramos salen de que el reclutador no lee las
# 300 postulaciones: lee las primeras 20 o 40 de la cola, y el ATS ordena esa
# cola en vez de rechazar por su cuenta. Llegar tarde no es que te filtren, es
# que te leen después de que ya entrevistaron a alguien.
#
# Las 96 horas son el umbral que aparece medido (TalentWorks, 1.600
# postulaciones); las magnitudes que publican las empresas del rubro —"8 veces
# más entrevistas"— son de blogs con interés en venderte urgencia, así que acá
# se usa la DIRECCIÓN del hallazgo, que es sólida, y no el número.
DEFECTOS_FRESCURA = {
    "hasta_24h": 25,
    "hasta_48h": 15,
    "hasta_96h": 8,
    "hasta_7d": 0,
    "mas_vieja": -10,
}

# Los tramos, en horas, de más nuevo a más viejo. El último atrapa todo lo demás.
TRAMOS_FRESCURA: tuple[tuple[str, float], ...] = (
    ("hasta_24h", 24),
    ("hasta_48h", 48),
    ("hasta_96h", 96),
    ("hasta_7d", 168),
    ("mas_vieja", float("inf")),
)


def _privado_del_entorno() -> dict:
    """El overlay privado servido como variable de entorno, en TOML.

    Un TOML mal escrito acá no puede tumbar la corrida: el cazador es un cron y
    el síntoma sería "hoy no llegó ningún aviso", que desde el teléfono se ve
    igual que "hoy no había nada". Se avisa al log y se sigue con el público.
    """
    crudo = os.environ.get("BYTE_PERFIL_PRIVADO", "").strip()
    if not crudo:
        return {}
    try:
        return tomllib.loads(crudo)
    except tomllib.TOMLDecodeError:
        logger.warning("perfil_privado_invalido")
        return {}


def cargar_criterio(ruta: Path) -> Criterio:
    """Lee el TOML. Si no está, devuelve el criterio por defecto.

    Que falte el archivo no puede ser un error: el cazador tiene que poder correr
    recién clonado el repo y que el usuario lo ajuste después de ver el primer
    aviso, no antes.
    """
    crudo = _leer_toml(ruta)
    # `privado.toml` se superpone al público y NO se versiona. Existe porque
    # este repo es público: algunas preferencias delatan cosas que no tienen por
    # qué leerse —dónde vas a estar viviendo, por ejemplo— y ponerlas en
    # `busqueda.toml` sería publicarlas. Las secciones se mezclan clave a clave,
    # así que el privado puede pisar un solo valor sin repetir el archivo.
    privado = _leer_toml(ruta.parent / "privado.toml")
    # Y el mismo overlay, pero desde el entorno. Hace falta para desplegar: si el
    # servicio se construye desde este repo, `privado.toml` no viaja —está en el
    # `.gitignore`, que es donde tiene que estar— y sin él lo presencial en el
    # país al que te mudás vuelve a restar 45 y se hunde, en silencio. Con esto
    # el dato viaja como variable del servicio y no como archivo publicado.
    privado = {**privado, **_privado_del_entorno()}
    for seccion, valores in privado.items():
        if isinstance(valores, dict) and isinstance(crudo.get(seccion), dict):
            crudo[seccion] = {**crudo[seccion], **valores}
        else:
            crudo[seccion] = valores

    situacion = crudo.get("situacion") or {}
    stack_crudo = crudo.get("stack") or {}
    stack = {
        grupo: tuple(str(t).lower() for t in (stack_crudo.get(grupo) or [])) for grupo in PESOS
    }
    preferencias = {**DEFECTOS_PREFERENCIAS, **_enteros(crudo.get("preferencias"))}
    penalizaciones = {**DEFECTOS_PENALIZACIONES, **_enteros(crudo.get("penalizaciones"))}
    aviso = crudo.get("aviso") or {}
    frescura = {**DEFECTOS_FRESCURA, **_enteros(crudo.get("frescura"))}
    empresas = _empresas(crudo.get("empresas"))
    return Criterio(
        stack=stack,
        pesos_stack=dict(PESOS),
        preferencias=preferencias,
        penalizaciones=penalizaciones,
        necesita_patrocinio=bool(situacion.get("necesita_patrocinio", False)),
        presencial_aceptable_en=tuple(
            str(x).strip().lower()
            for x in (situacion.get("presencial_aceptable_en") or [])
            if str(x).strip()
        ),
        fuentes={k: bool(v) for k, v in (crudo.get("fuentes") or {}).items()},
        frescura=frescura,
        empresas=empresas,
        tope_por_aviso=int(aviso.get("tope_por_aviso", 8)),
        puntaje_minimo=int(aviso.get("puntaje_minimo", 25)),
        tope_por_empresa=int(aviso.get("tope_por_empresa", 2)),
        descartar_despues_de_dias=int(aviso.get("descartar_despues_de_dias", 0)),
        horas_sin_aviso=int(aviso.get("horas_sin_aviso", 24)),
    )


def _leer_toml(ruta: Path) -> dict:
    """El TOML, o vacío. Que falte o esté roto no puede frenar la búsqueda."""
    if not ruta.is_file():
        return {}
    try:
        return tomllib.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _empresas(seccion: object) -> tuple[tuple[str, str, str], ...]:
    """El `[[empresas]]` del TOML, saltando las entradas incompletas.

    Una fila a la que le falta el token es una empresa que no se va a poder
    consultar; dejarla entrar sólo produce un error por corrida que no dice
    nada nuevo.
    """
    if not isinstance(seccion, list):
        return ()
    salida = []
    for fila in seccion:
        if not isinstance(fila, dict):
            continue
        nombre = str(fila.get("nombre", "")).strip()
        ats = str(fila.get("ats", "")).strip().lower()
        token = str(fila.get("token", "")).strip()
        if nombre and ats and token:
            salida.append((nombre, ats, token))
    return tuple(salida)


def _enteros(seccion: object) -> dict[str, int]:
    if not isinstance(seccion, dict):
        return {}
    salida: dict[str, int] = {}
    for clave, valor in seccion.items():
        try:
            salida[str(clave)] = int(valor)
        except (TypeError, ValueError):
            continue
    return salida


def _patron(termino: str) -> re.Pattern[str]:
    """Un término del stack, como expresión que no pesque pedazos de palabra.

    Los términos con puntuación —`.net`, `c#`, `next.js`— no llevan borde por la
    izquierda a propósito: con él, `.net` no encontraría "ASP.NET", que es donde
    aparece la mitad de las veces.
    """
    escapado = re.escape(termino)
    izquierda = r"(?<![a-z0-9])" if termino[0].isalnum() else ""
    return re.compile(rf"{izquierda}{escapado}(?![a-z0-9])")


_CACHE_PATRONES: dict[str, re.Pattern[str]] = {}


def patron_de(termino: str) -> re.Pattern[str]:
    if termino not in _CACHE_PATRONES:
        _CACHE_PATRONES[termino] = _patron(termino)
    return _CACHE_PATRONES[termino]


@dataclass(frozen=True, slots=True)
class Puntaje:
    """El número y las líneas que lo explican."""

    total: int
    motivos: tuple[str, ...]
    senales: tuple[str, ...]
    terminos: tuple[str, ...]
    antiguedad_horas: float | None = None


def detectar_senales(oferta: Oferta, aceptable_en: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Qué señales trae el texto. `sin_patrocinio` anula a `patrocinio`."""
    texto = oferta.buscable()
    encontradas = [nombre for nombre, patron in SENALES.items() if patron.search(texto)]
    if "sin_patrocinio" in encontradas and "patrocinio" in encontradas:
        encontradas.remove("patrocinio")
    # Una oferta que declara que no hay oficina no es híbrida ni presencial por
    # nombrar esas palabras para negarlas.
    # Lo mismo con la reubicación: nombrarla para negarla no es ofrecerla.
    if "reubicacion" in encontradas and NIEGA_REUBICACION.search(texto):
        encontradas.remove("reubicacion")
    if NIEGA_OFICINA.search(texto):
        encontradas = [s for s in encontradas if s not in ("hibrido", "presencial")]
    # Ir a una oficina sólo es un problema si la oficina está donde no vas a
    # estar. Un presencial en Caracas no es el mismo puesto que uno en Santiago
    # para alguien que se muda a Venezuela: el primero es aplicable y el segundo
    # no, y sin esto los dos se hundían igual.
    elif aceptable_en and any(pais in texto for pais in aceptable_en):
        encontradas = [s for s in encontradas if s not in ("hibrido", "presencial")]
    # Upwork es freelance por definición: el texto de la oferta no tiene por qué
    # decirlo y perderíamos la señal.
    if oferta.fuente == "upwork" and "freelance" not in encontradas:
        encontradas.append("freelance")
    return tuple(encontradas)


def tramo_de_frescura(horas: float | None) -> str | None:
    """En qué tramo cae una oferta. `None` si el feed no mandó fecha."""
    if horas is None:
        return None
    return next(nombre for nombre, tope in TRAMOS_FRESCURA if horas <= tope)


def puntuar(oferta: Oferta, criterio: Criterio, ahora: datetime | None = None) -> Puntaje:
    """El puntaje de una oferta, con el detalle de de dónde salió cada punto."""
    texto = oferta.buscable()
    motivos: list[str] = []

    del_stack = 0
    encontrados: list[str] = []
    for grupo, peso in criterio.pesos_stack.items():
        for termino in criterio.stack.get(grupo, ()):
            if patron_de(termino).search(texto):
                del_stack += peso
                encontrados.append(termino)
    if del_stack > criterio.tope_stack:
        motivos.append(f"+{criterio.tope_stack} stack (tope; sumaba {del_stack})")
        del_stack = criterio.tope_stack
    elif del_stack:
        motivos.append(f"+{del_stack} stack: {', '.join(encontrados[:8])}")

    total = del_stack
    senales = detectar_senales(oferta, criterio.presencial_aceptable_en)

    for senal in senales:
        puntos = criterio.preferencias.get(senal, 0)
        if puntos:
            total += puntos
            motivos.append(f"+{puntos} {senal}")

    for senal in senales:
        castigo = criterio.penalizaciones.get(senal, 0)
        # "no patrocina" solo importa si hoy necesitás que alguien patrocine.
        # Para alguien que ya puede trabajar donde está, es una línea de más en
        # el aviso, no un problema.
        if senal == "sin_patrocinio" and not criterio.necesita_patrocinio:
            continue
        if castigo:
            total -= castigo
            motivos.append(f"-{castigo} {senal}")

    # La frescura va al final para que quede última en los motivos: es la que
    # se mira primero cuando una oferta buena aparece baja en la lista.
    horas = oferta.antiguedad_horas(ahora)
    tramo = tramo_de_frescura(horas)
    if tramo is not None:
        puntos = criterio.frescura.get(tramo, 0)
        if puntos:
            total += puntos
            motivos.append(f"{puntos:+d} {tramo} ({horas:.0f} h)")
    else:
        # Sin fecha no se premia ni se castiga. Castigar convertiría "este feed
        # no manda la fecha" en "esta oferta es vieja", que son cosas distintas.
        motivos.append("sin fecha de publicación")

    return Puntaje(
        total=total,
        motivos=tuple(motivos),
        senales=senales,
        terminos=tuple(encontrados),
        antiguedad_horas=horas,
    )


@dataclass(frozen=True, slots=True)
class Brecha:
    """Qué pide la oferta, qué de eso está en tu CV y qué no.

    Es una diferencia de conjuntos, no una opinión del modelo. Sirve para lo
    único que se decide al postular: qué va en las dos primeras líneas —lo que
    piden y tenés— y qué conviene nombrar aunque sea de refilón, porque lo piden
    y tu CV hoy no lo dice.

    No mide si sabés algo: mide si tu CV lo **dice**. Un reclutador busca
    términos en un buscador, y lo que no está escrito no aparece.
    """

    pide: tuple[str, ...]
    tenes: tuple[str, ...]
    faltan: tuple[str, ...]

    @property
    def cobertura(self) -> float:
        """Qué proporción de lo que pide la oferta figura en tu CV."""
        return len(self.tenes) / len(self.pide) if self.pide else 0.0


def comparar_con_cv(oferta: Oferta, cv: str, vocabulario: tuple[str, ...]) -> Brecha:
    """Cruza los términos de la oferta contra el texto del CV."""
    texto_oferta = oferta.buscable()
    texto_cv = cv.lower()
    pide = [t for t in vocabulario if patron_de(t).search(texto_oferta)]
    tenes = [t for t in pide if patron_de(t).search(texto_cv)]
    faltan = [t for t in pide if t not in tenes]
    return Brecha(pide=tuple(pide), tenes=tuple(tenes), faltan=tuple(faltan))
