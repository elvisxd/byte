"""El cazador: trae, puntúa, recuerda y avisa. Un cron, no un endpoint.

    uv run python -m empleo.cazador            # una vuelta y avisa
    uv run python -m empleo.cazador --probar   # qué devuelve cada fuente, sin avisar
    uv run python -m empleo.cazador --sin-avisar

**Lo que no hace, y no va a hacer: postular.** Junta links y los ordena. Abrir
el link, leer la oferta y decidir si va tu tiempo ahí es tuyo — igual que el CV,
que Byte edita pero no publica. En Upwork esto no es una preferencia de diseño:
el auto-envío de propuestas se paga con suspensión permanente de la cuenta.
"""

import argparse
import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import httpx

# `api.config` se importa por su efecto al cargarse: hace `load_dotenv()`, y sin
# eso el cazador corrido a mano o desde el cron no ve el `.env` —PANEL_URL entre
# otras— y el aviso nunca sale del disco. Los vigías de `paper/` no lo notaban
# porque su lanzador exporta las variables antes de arrancarlos.
import api.config  # noqa: F401
from api.logging import get_logger
from empleo import fuentes
from empleo.aviso import avisar
from empleo.criterio import Criterio, Puntaje, cargar_criterio, puntuar
from empleo.memoria import Memoria, YaCorriendo, turno
from empleo.oferta import Oferta
from empleo.postulaciones import Respuesta, bloque, inventario
from empleo.retraso import informe, leer_avisos, medir, parte, resumen, seguir

logger = get_logger("empleo.cazador")

# Cuántos días de buzón mira el seguimiento. Más que `--retraso` (45) porque un
# proceso largo —cuatro rondas y una oferta— cruza los dos meses sin problema, y
# el parte tiene que poder mostrar esa conversación entera y no su último tramo.
DIAS_DE_SEGUIMIENTO = 90

RAIZ = Path(__file__).resolve().parent.parent
CRITERIO_POR_DEFECTO = RAIZ / "perfil" / "busqueda.toml"


def carpeta_de_trabajo() -> Path:
    """Dónde van el digest y la memoria.

    Fuera del repo por defecto: son datos que cambian cada día y links de
    puestos ajenos, no código. Que no ensucien el `git status` es parte de que
    esto se pueda dejar corriendo en un cron sin pensarlo más.
    """
    return Path(os.environ.get("BYTE_EMPLEO_DIR", Path.home() / ".byte" / "empleo"))


async def recolectar(
    criterio: Criterio, consulta_upwork: str
) -> tuple[list[Oferta], dict[str, int]]:
    """Todas las fuentes encendidas, en paralelo. Devuelve las ofertas y el conteo.

    El conteo importa tanto como las ofertas: "hoy no llegó nada" y "hoy falló
    RemoteOK" se ven igual desde el teléfono, y son problemas distintos.
    """
    activas: dict[str, Callable[[httpx.AsyncClient], Awaitable[list[Oferta]]]] = {}
    if criterio.fuentes.get("remoteok", True):
        activas["remoteok"] = fuentes.remoteok
    if criterio.fuentes.get("remotive", True):
        activas["remotive"] = fuentes.remotive
    if criterio.fuentes.get("weworkremotely", True):
        activas["weworkremotely"] = fuentes.weworkremotely
    if criterio.fuentes.get("hackernews", True):
        activas["hackernews"] = fuentes.hackernews
    if criterio.fuentes.get("getonbrd", True):
        # Las búsquedas salen de los términos `fuerte` del TOML: su API exige
        # `query`, y tener una segunda lista en el código sería el mismo
        # criterio escrito en dos lugares que se desincronizan.
        consultas = criterio.stack.get("fuerte", ())
        activas["getonbrd"] = lambda c: fuentes.getonbrd(c, consultas)
    # Workday va aparte de las otras tres: necesita los términos que buscás para
    # decidir de qué ofertas vale la pena pedir la descripción, porque su
    # listado no la trae. Ver el comentario largo en `fuentes.py`.
    en_workday = tuple(e for e in criterio.empresas if e[1] == "workday")
    if criterio.fuentes.get("workday", True) and en_workday:
        terminos = criterio.stack.get("fuerte", ()) + criterio.stack.get("medio", ())
        activas["workday"] = lambda c: fuentes.workday(c, en_workday, terminos)
    # Las empresas que no contestaron. Se llena adentro del adaptador y se lee
    # después de la corrida: un token equivocado no puede quedarse callado.
    mudas: list[str] = []
    if criterio.fuentes.get("empresas", True) and criterio.empresas:
        # Las empresas grandes rara vez publican en los agregadores: se les
        # pregunta a su propia página de Careers, que es de donde salen los
        # puestos el día que abren.
        listado = tuple(e for e in criterio.empresas if e[1] != "workday")
        if listado:
            activas["empresas"] = lambda c: fuentes.empresas(c, listado, mudas)
    # Las dos fuentes de correo comparten buzón y contraseña: se leen una vez,
    # acá afuera, porque encender Job Bank con LinkedIn apagado es una
    # combinación legítima —y con las credenciales adentro del `if` de LinkedIn
    # sería un `NameError` en la primera vuelta.
    usuario = os.environ.get("GMAIL_USUARIO", "")
    clave = os.environ.get("GMAIL_APP_PASSWORD", "")
    if criterio.fuentes.get("linkedin", False):
        # `imaplib` es síncrona: en el bucle bloquearía a las otras cinco
        # fuentes mientras negocia TLS y descarga treinta correos.
        activas["linkedin"] = lambda _c: asyncio.to_thread(
            fuentes.linkedin_por_imap, usuario, clave
        )
    if criterio.fuentes.get("jobbank", False):
        # Misma razón que arriba para el hilo: es otra conexión IMAP.
        activas["jobbank"] = lambda _c: asyncio.to_thread(fuentes.jobbank_por_imap, usuario, clave)
    if criterio.fuentes.get("upwork", False):
        token = os.environ.get("UPWORK_TOKEN", "")
        activas["upwork"] = lambda c: fuentes.upwork(c, token, consulta_upwork)

    async with fuentes.cliente_http() as cliente:
        resultados = await asyncio.gather(
            *(adaptador(cliente) for adaptador in activas.values()),
            return_exceptions=True,
        )

    ofertas: list[Oferta] = []
    conteo: dict[str, int] = {}
    for nombre, resultado in zip(activas, resultados, strict=True):
        if isinstance(resultado, BaseException):
            logger.warning("fuente_excepcion", fuente=nombre, error_type=type(resultado).__name__)
            conteo[nombre] = -1
            continue
        conteo[nombre] = len(resultado)
        ofertas.extend(resultado)
    # Cada empresa muda entra al conteo con su propio nombre y en -1, que es el
    # mismo "error" que ya usa el pie del aviso para una fuente caída. Así el
    # token roto se lee en el teléfono —"empresa Shopify: error"— en vez de
    # esconderse dentro de un total de `empresas` que igual parece sano.
    if mudas:
        # Con las catorce caídas a la vez —un corte de red, o el ATS
        # frenándote— esta línea medía 498 caracteres, y el aviso entero 1.318
        # contra un tope de 950: el pie se recortaba y se perdía el conteo de
        # las fuentes que SÍ trajeron algo. Se nombran las primeras y se cuenta
        # el resto; la lista completa queda en el log y en el digest.
        for empresa in mudas[:TOPE_MUDAS_EN_EL_PIE]:
            conteo[f"empresa {empresa}"] = -1
        if len(mudas) > TOPE_MUDAS_EN_EL_PIE:
            conteo[f"+{len(mudas) - TOPE_MUDAS_EN_EL_PIE} empresas mudas"] = -1
    return ofertas, conteo


def seleccionar(
    ofertas: list[Oferta], criterio: Criterio, memoria: Memoria
) -> list[tuple[Oferta, Puntaje]]:
    """Puntúa, saca las repetidas y ordena de mejor a peor.

    La deduplicación mira la clave de la fuente **y** la huella empresa+puesto:
    la misma búsqueda publicada en dos boards es una oportunidad, no dos.
    """
    vistas_en_esta_vuelta: set[str] = set()
    seleccion: list[tuple[Oferta, Puntaje]] = []
    for oferta in ofertas:
        if memoria.conoce(oferta.clave, oferta.huella):
            continue
        if oferta.huella in vistas_en_esta_vuelta:
            continue
        vistas_en_esta_vuelta.add(oferta.huella)
        seleccion.append((oferta, puntuar(oferta, criterio)))
    seleccion.sort(key=lambda par: par[1].total, reverse=True)
    return seleccion


def _edad(horas: float | None) -> str:
    """La antigüedad, como se lee de un vistazo en el teléfono.

    Va primero en la línea junto al puntaje porque es lo que decide si abrís el
    link ahora o después: una de hace 3 horas y una de hace 9 días se postulan
    distinto aunque puntúen parecido.
    """
    if horas is None:
        return "s/f"
    if horas < 1:
        return "recién"
    if horas < 48:
        return f"{horas:.0f}h"
    return f"{horas / 24:.0f}d"


def _linea(oferta: Oferta, puntaje: Puntaje) -> str:
    empresa = f" — {oferta.empresa}" if oferta.empresa else ""
    senales = f"  [{', '.join(puntaje.senales)}]" if puntaje.senales else ""
    lugar = f"\n  {oferta.ubicacion}" if oferta.ubicacion else ""
    edad = _edad(puntaje.antiguedad_horas)
    return (
        f"{puntaje.total:>4} · {edad:>6}  {oferta.titulo}{empresa}{senales}{lugar}\n  {oferta.url}"
    )


def _dignas(
    seleccion: list[tuple[Oferta, Puntaje]], criterio: Criterio
) -> list[tuple[Oferta, Puntaje]]:
    """Las que llegan al teléfono: puntaje suficiente y todavía a tiempo."""
    return [
        par
        for par in seleccion
        if par[1].total >= criterio.puntaje_minimo and _bastante_fresca(par[1], criterio)
    ]


def armar_aviso(
    seleccion: list[tuple[Oferta, Puntaje]], criterio: Criterio, conteo: dict[str, int]
) -> str:
    """El mensaje que llega al teléfono."""
    dignas = _dignas(seleccion, criterio)
    cabecera = f"Ofertas — {datetime.now().strftime('%d/%m %H:%M')}"
    if not dignas:
        revisadas = sum(n for n in conteo.values() if n > 0)
        return (
            f"{cabecera}\nNada sobre {criterio.puntaje_minimo} puntos "
            f"entre {revisadas} ofertas nuevas.\n{_pie_fuentes(conteo)}"
        )

    muestra = _repartir(dignas, criterio.tope_por_empresa)[: criterio.tope_por_aviso]
    encabezado = f"{cabecera} — {len(dignas)} nuevas"
    pie = _pie_fuentes(conteo)

    # Acá se iban tirando ofertas del final hasta que el mensaje entrara en un
    # solo POST, porque el panel rechazaba de más de 1.000 caracteres y el pie
    # —el conteo de fuentes, la línea que dice si LinkedIn o Job Bank se
    # cayeron— es lo último del texto.
    #
    # Ya no hace falta: `avisar()` parte el aviso en varios mensajes cuando no
    # entra en uno, cortando entre ofertas. Lo que decide cuántas ofertas van
    # al teléfono vuelve a ser sólo el criterio —`tope_por_aviso` y
    # `tope_por_empresa`, que están en el TOML y se revisan en un diff— y no el
    # ancho del canal.
    lineas = [_linea(o, p) for o, p in muestra]
    faltan = len(dignas) - len(lineas)
    extra = f"\n\n(+{faltan} más en el digest)" if faltan > 0 else ""
    return f"{encabezado}\n\n" + "\n\n".join(lineas) + extra + f"\n\n{pie}"


# Cuántas empresas mudas se nombran en el pie del aviso antes de resumirlas.
# Tres entran sin comerse una oferta; catorce no.
TOPE_MUDAS_EN_EL_PIE = 3

ULTIMO_AVISO = "ultimo_aviso.txt"


def _horas_de_silencio(carpeta: Path) -> float | None:
    """Cuánto hace que no se manda nada. `None` si no hay registro.

    Vive en un archivo y no en memoria porque cada vuelta es un proceso nuevo
    —en Railway, un contenedor nuevo—. Sin el volumen montado esto se pierde en
    cada vuelta y el resultado es que la línea de "sigo vivo" sale siempre, que
    es el comportamiento seguro: de más, nunca de menos.
    """
    try:
        cuando = datetime.fromisoformat((carpeta / ULTIMO_AVISO).read_text("utf-8").strip())
    except (OSError, ValueError):
        return None
    return (datetime.now() - cuando).total_seconds() / 3600


def _anotar_aviso(carpeta: Path) -> None:
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / ULTIMO_AVISO).write_text(datetime.now().isoformat(), encoding="utf-8")
    except OSError as exc:
        # No poder anotarlo sólo hace que la próxima vuelta mande un "sigo vivo"
        # de más. No vale tumbar la vuelta por eso.
        logger.warning("ultimo_aviso_no_escrito", error_type=type(exc).__name__)


def texto_para_telegram(
    seleccion: list[tuple[Oferta, Puntaje]],
    criterio: Criterio,
    conteo: dict[str, int],
    horas_de_silencio: float | None,
    pendientes: str = "",
) -> str:
    """Lo que se manda al teléfono, o `""` si esta vuelta no merece interrumpir.

    Cinco veces por día hábil, un "no encontré nada" no es información: es el
    mensaje que te enseña a no abrir el canal, y entonces el día que llega uno
    bueno tampoco lo abrís. Así que si no hay nada, no se manda nada.

    Pero el silencio miente en dos casos, y los dos se avisan igual:

    1. **Ninguna fuente trajo nada.** Eso no es "hoy no había ofertas", es que el
       cazador está ciego —se cayó la red, cambió un feed, venció una key— y
       desde el teléfono se ve idéntico a un día tranquilo.
    2. **Hace demasiado que no se manda nada.** Un cron muerto también se ve
       idéntico a un día tranquilo. Una línea cada `horas_sin_aviso` alcanza
       para distinguirlos, y sigue siendo una en vez de cinco.
    """
    # Lo que tus postulaciones piden interrumpe SIEMPRE, haya ofertas o no. Es
    # trabajo tuyo con fecha de vencimiento —un video que no mandaste, un
    # formulario a medias— y perderlo cuesta una postulación entera, mientras
    # que perder una oferta cuesta una de las varias que salen cada día.
    if _dignas(seleccion, criterio):
        aviso = armar_aviso(seleccion, criterio, conteo)
        return f"{pendientes}\n\n{aviso}" if pendientes else aviso
    if pendientes:
        return f"{pendientes}\n\n{_pie_fuentes(conteo)}"

    revisadas = sum(n for n in conteo.values() if n > 0)
    if conteo and not revisadas:
        return (
            f"Ofertas — {datetime.now().strftime('%d/%m %H:%M')}\n"
            f"Ninguna fuente devolvió nada. No es que no haya ofertas: "
            f"algo se rompió.\n{_pie_fuentes(conteo)}"
        )

    if criterio.horas_sin_aviso > 0 and (
        horas_de_silencio is None or horas_de_silencio >= criterio.horas_sin_aviso
    ):
        return (
            f"Ofertas — {datetime.now().strftime('%d/%m %H:%M')}\n"
            f"Sigo mirando. Nada sobre {criterio.puntaje_minimo} puntos "
            f"desde el último aviso, entre {revisadas} revisadas en esta vuelta.\n"
            f"{_pie_fuentes(conteo)}"
        )
    return ""


def _bastante_fresca(puntaje: Puntaje, criterio: Criterio) -> bool:
    """Si la oferta llegó a tiempo como para que valga postularse.

    Restar puntos no alcanza para las viejas: una oferta que menciona todo el
    stack absorbe la penalización de frescura y sigue arriba del aviso. Medido
    el 17/09/2026, dos de las cuatro que llegaban al teléfono tenían 29 días —
    a esa altura el reclutador ya entrevistó a alguien.

    Sin fecha se deja pasar: que un feed no la mande no la vuelve vieja, y
    descartarla castigaría a la fuente, no a la oferta.
    """
    if criterio.descartar_despues_de_dias <= 0:
        return True
    if puntaje.antiguedad_horas is None:
        return True
    return puntaje.antiguedad_horas <= criterio.descartar_despues_de_dias * 24


def _repartir(
    dignas: list[tuple[Oferta, Puntaje]], tope_por_empresa: int
) -> list[tuple[Oferta, Puntaje]]:
    """Las mismas ofertas, sin dejar que una empresa se lleve el aviso entero.

    Los marketplaces de talento republican su catálogo todo el tiempo y con
    puntajes altos: medido el 16/09/2026, Lemon.io ocupaba cinco de los ocho
    lugares con ofertas de 8 a 29 días, y la única fresca del día —12 horas,
    remota, AI agent engineer— entraba cuarta.

    No se descarta nada: lo que pasa el tope baja al final de la lista, así que
    si sobra lugar igual aparece. El orden por puntaje se conserva dentro de
    cada grupo.
    """
    if tope_por_empresa <= 0:
        return dignas
    dentro: list[tuple[Oferta, Puntaje]] = []
    fuera: list[tuple[Oferta, Puntaje]] = []
    vistas: dict[str, int] = {}
    for oferta, puntaje in dignas:
        # Sin empresa no se agrupa: en Hacker News la empresa sale de la primera
        # línea del comentario y a veces queda vacía. Agruparlas todas bajo ""
        # dejaría fuera ofertas que no tienen nada que ver entre sí.
        clave = oferta.empresa.strip().casefold()
        if not clave:
            dentro.append((oferta, puntaje))
            continue
        vistas[clave] = vistas.get(clave, 0) + 1
        destino = dentro if vistas[clave] <= tope_por_empresa else fuera
        destino.append((oferta, puntaje))
    return dentro + fuera


def _pie_fuentes(conteo: dict[str, int]) -> str:
    partes = [f"{n}: {'error' if c < 0 else c}" for n, c in sorted(conteo.items())]
    return "fuentes → " + " · ".join(partes)


def escribir_digest(
    carpeta: Path, seleccion: list[tuple[Oferta, Puntaje]], conteo: dict[str, int]
) -> Path:
    """Todo lo de la vuelta, también lo que no llegó al teléfono.

    Es donde se ve si el criterio quedó demasiado duro: si semana tras semana el
    digest tiene cosas buenas que el aviso no mandó, el que está mal es el
    puntaje mínimo, no el feed.
    """
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{datetime.now().strftime('%Y-%m-%d-%H%M')}.md"
    lineas = [
        f"# Ofertas — {datetime.now().isoformat(timespec='minutes')}",
        "",
        _pie_fuentes(conteo),
        "",
    ]
    for oferta, puntaje in seleccion:
        lineas += [
            f"## {puntaje.total} · {oferta.titulo}"
            + (f" — {oferta.empresa}" if oferta.empresa else ""),
            f"- {oferta.url}",
            f"- fuente: {oferta.fuente}" + (f" · {oferta.ubicacion}" if oferta.ubicacion else ""),
            f"- por qué: {'; '.join(puntaje.motivos) or 'nada que sume'}",
        ]
        # Qué alerta la trajo, cuando la fuente lo dice. Va acá y no al aviso
        # de Telegram: el aviso se lee de un vistazo, y una línea más por
        # oferta es una pantalla más de scroll para un dato que sólo sirve
        # cuando te sentás a revisar qué alerta conviene borrar.
        #
        # Es lo que hace revisable una lista de diez alertas: si una llena el
        # digest de puestos que no tienen nada que ver, se ve acá y se borra en
        # LinkedIn.
        if oferta.origen:
            lineas.append(f"- alerta: {oferta.origen}")
        lineas.append("")
    destino.write_text("\n".join(lineas), encoding="utf-8")
    return destino


async def _pendientes(criterio: Criterio, carpeta: Path) -> list[Respuesta]:
    """Lo que tus postulaciones piden y todavía no te avisé.

    Memoria aparte de la de las ofertas: son cosas distintas con vidas distintas,
    y mezclarlas haría que borrar una borre la otra. Se anota el Message-ID, que
    es único por correo y no cambia porque lo leas.

    `imaplib` es síncrona, así que va a un hilo igual que la fuente de LinkedIn:
    una conexión IMAP en el bucle deja esperando a todo lo demás.
    """
    if not criterio.fuentes.get("postulaciones", True):
        return []
    usuario = os.environ.get("GMAIL_USUARIO", "")
    clave = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not usuario or not clave:
        return []

    respuestas = await asyncio.to_thread(fuentes.respuestas_por_imap, usuario, clave)
    memoria = Memoria(carpeta / "postulaciones.json")
    # `interrumpe` y no `estado in AVISABLES`: un contacto con señales de estafa
    # sigue apareciendo en `--correos`, con las señales a la vista, pero no te
    # despierta el teléfono. Un canal que te interrumpe con fraude es un canal
    # que dejás de abrir.
    nuevas = [r for r in respuestas if r.interrumpe and not memoria.conoce(r.id_mensaje)]
    # Se anota acá y no después de avisar, al revés que las ofertas: un pendiente
    # repetido cinco veces por día es peor que uno perdido, porque el correo
    # sigue en tu bandeja mientras que la oferta caduca.
    for r in nuevas:
        memoria.anotar(r.id_mensaje)
    if nuevas:
        memoria.guardar()
    logger.info("postulaciones_revisadas", total=len(respuestas), pendientes=len(nuevas))
    return nuevas


async def una_vuelta(
    criterio: Criterio, carpeta: Path, consulta_upwork: str, con_aviso: bool
) -> str:
    ofertas, conteo = await recolectar(criterio, consulta_upwork)
    memoria = Memoria(carpeta / "vistas.json")
    seleccion = seleccionar(ofertas, criterio, memoria)
    destino = escribir_digest(carpeta, seleccion, conteo)
    # Dos textos distintos a propósito: el de la consola cuenta siempre qué pasó
    # —corrés el comando, querés ver el resultado— y el del teléfono sólo
    # interrumpe cuando hay algo que decir.
    texto = armar_aviso(seleccion, criterio, conteo)
    pendientes = bloque(await _pendientes(criterio, carpeta))
    al_telefono = texto_para_telegram(
        seleccion, criterio, conteo, _horas_de_silencio(carpeta), pendientes
    )

    if con_aviso and al_telefono:
        # Se anota **después** de avisar y solo lo que se avisó: si el panel está
        # caído, estas ofertas tienen que volver a aparecer en la próxima vuelta.
        if avisar(al_telefono):
            _anotar_aviso(carpeta)
            for oferta, puntaje in seleccion:
                if puntaje.total >= criterio.puntaje_minimo:
                    memoria.anotar(oferta.clave, oferta.huella)
            memoria.guardar()
    elif con_aviso:
        # Queda en el log: una vuelta silenciosa y una que no corrió se
        # distinguen mirando acá, que es lo que el teléfono ya no distingue.
        logger.info("vuelta_silenciosa", nuevas=len(seleccion))
    logger.info("vuelta_terminada", nuevas=len(seleccion), digest=str(destino), **conteo)
    return texto


async def probar(criterio: Criterio, consulta_upwork: str) -> str:
    """Qué devuelve cada fuente, sin deduplicar ni avisar.

    Existe porque los feeds cambian de forma sin avisar y porque el adaptador de
    Upwork se escribió contra la documentación, no contra el servidor: cuando la
    key esté aprobada, esto dice en una corrida si la respuesta llega como se
    esperaba.
    """
    ofertas, conteo = await recolectar(criterio, consulta_upwork)
    lineas = [_pie_fuentes(conteo), ""]
    por_fuente: dict[str, Oferta] = {}
    for oferta in ofertas:
        por_fuente.setdefault(oferta.fuente, oferta)
    for nombre, muestra in sorted(por_fuente.items()):
        puntaje = puntuar(muestra, criterio)
        lineas += [
            f"[{nombre}] {muestra.titulo} — {muestra.empresa}",
            f"  url: {muestra.url}",
            f"  ubicación: {muestra.ubicacion or '(vacía)'}",
            f"  descripción: {len(muestra.descripcion)} caracteres",
            f"  puntaje: {puntaje.total} · {'; '.join(puntaje.motivos) or 'nada'}",
            "",
        ]
    return "\n".join(lineas)


async def probar_empresas(criterio: Criterio) -> str:
    """Cuáles de los tokens de `[[empresas]]` responden, y con cuántos puestos.

    Existe porque un token equivocado falla en silencio: la empresa aporta cero
    ofertas y eso se ve igual que "hoy no publicó nada". Acá se ven las dos
    cosas separadas, que es lo único que permite corregir la lista.
    """
    if not criterio.empresas:
        return "No hay empresas en `[[empresas]]` del TOML."

    # Workday no se pregunta por el mismo camino que las otras tres: necesita
    # los términos que buscás para decidir de qué puestos pedir la descripción.
    # Pasarlo por `empresas()` —que sólo conoce greenhouse, lever y ashby— lo
    # reportaba como "token equivocado" cuando el token estaba bien. Una
    # herramienta que existe para distinguir un fallo real de uno aparente no
    # puede inventar el suyo.
    terminos = criterio.stack.get("fuerte", ()) + criterio.stack.get("medio", ())

    lineas = []
    async with fuentes.cliente_http() as cliente:
        for entrada in criterio.empresas:
            nombre, ats, token = entrada
            if ats.lower() == "workday":
                encontradas = await fuentes.workday(cliente, (entrada,), terminos)
            else:
                encontradas = await fuentes.empresas(cliente, (entrada,))
            if encontradas:
                estado = f"{len(encontradas):>3} puestos"
                muestra = f"  ej: {encontradas[0].titulo[:60]}"
            else:
                estado = "  sin respuesta o token equivocado"
                # El código HTTP sale en el log de arriba (`fuente_fallo`), y es
                # lo que decide qué hacer: 404 es el token, 403 o 429 es que te
                # están frenando y la empresa puede estar bien.
                muestra = f"  mirá el `status` del aviso de arriba ({ats}/{token})"
            lineas.append(f"[{ats:>10}] {nombre:<16} {estado}\n{muestra}")
    return "\n".join(lineas)


async def agregar_empresa(nombre: str, url: str) -> str:
    """Resuelve la URL de una página de empleos, la prueba, e imprime el TOML.

    No escribe en el archivo a propósito: `busqueda.toml` lleva comentarios que
    explican cada decisión, y un script que edita eso los pierde. Imprime el
    bloque para pegar, que es un paso y no se equivoca.
    """
    identificada = fuentes.identificar_empresa(url)
    if identificada is None:
        return (
            f"No reconozco esa URL: {url[:120]}\n"
            "Tienen que ser la página de empleos de la empresa, del estilo\n"
            "  boards.greenhouse.io/TOKEN · jobs.lever.co/TOKEN\n"
            "  jobs.ashbyhq.com/TOKEN · EMPRESA.wdN.myworkdayjobs.com/SITIO"
        )

    ats, token = identificada
    entrada = (nombre, ats, token)
    async with fuentes.cliente_http() as cliente:
        if ats == "workday":
            encontradas = await fuentes.workday(cliente, (entrada,), ())
        else:
            encontradas = await fuentes.empresas(cliente, (entrada,))

    if not encontradas:
        return (
            f"Reconocí {ats}/{token}, pero no devolvió ningún puesto.\n"
            "Puede ser que no tenga vacantes abiertas, o que la URL no sea la de "
            "su board. Probá abrirla en el navegador y copiar la de la lista de "
            "puestos, no la de la página de marketing."
        )

    comillas = '"'
    return (
        f"{len(encontradas)} puestos. Ejemplo: {encontradas[0].titulo[:70]}\n\n"
        f"Pegá esto en perfil/busqueda.toml:\n\n"
        f"[[empresas]]\n"
        f"nombre = {comillas}{nombre}{comillas}\n"
        f"ats = {comillas}{ats}{comillas}\n"
        f"token = {comillas}{token}{comillas}"
    )


async def medir_retraso(criterio: Criterio, carpeta: Path, al_telefono: bool = False) -> str:
    """Cuánto tardás en postular después del aviso. No cambia nada, mide.

    Lee los digests del disco y el buzón, y los resta. Ver `empleo/retraso.py`
    para por qué esto no vive dentro del criterio: primero se mide y después se
    decide si `hasta_24h` vale 25 o vale menos.
    """
    usuario = os.environ.get("GMAIL_USUARIO", "")
    clave = os.environ.get("GMAIL_APP_PASSWORD", "")
    # Sin credenciales no hay buzón, y sin buzón TODAS las ofertas figuran sin
    # postular. Ese número se lee como "ignoraste 121 ofertas" cuando es "no
    # miramos", así que el informe tiene que saber la diferencia.
    sin_buzon = "" if usuario and clave else "faltan GMAIL_USUARIO / GMAIL_APP_PASSWORD"
    respuestas = (
        []
        if sin_buzon
        else await asyncio.to_thread(fuentes.respuestas_por_imap, usuario, clave, 45)
    )
    medicion = medir(
        leer_avisos(carpeta),
        respuestas,
        criterio.puntaje_minimo,
        # "A tiempo" es el mismo corte con el que el cazador decide no avisar
        # una oferta por vieja. Si acá fuera otro, dirían cosas distintas.
        (criterio.descartar_despues_de_dias or 4) * 24,
        sin_buzon,
    )
    if al_telefono:
        # Los digests y el buzón viven los dos en Railway, y la salida de una
        # corrida allá no siempre se puede leer. El panel sí llega, y es el
        # camino que este servicio ya usa todos los días.
        avisar(resumen(medicion))
    return informe(medicion)


async def ver_seguimiento(criterio: Criterio, carpeta: Path, al_telefono: bool) -> str:
    """En qué quedó cada postulación. Sólo lee: digests y buzón.

    Comparte el camino con `--retraso` a propósito —son el mismo cruce— pero
    contestan cosas distintas: aquél mide cuánto tardás en postular, éste dice
    qué pasó después.
    """
    usuario = os.environ.get("GMAIL_USUARIO", "")
    clave = os.environ.get("GMAIL_APP_PASSWORD", "")
    # Mismo aviso que en `medir_retraso`: sin buzón TODO figura sin rastro, y
    # ese número se lee como "no postulaste a ninguna" cuando dice "no miramos".
    sin_buzon = "" if usuario and clave else "faltan GMAIL_USUARIO / GMAIL_APP_PASSWORD"
    respuestas = (
        []
        if sin_buzon
        else await asyncio.to_thread(
            fuentes.respuestas_por_imap, usuario, clave, DIAS_DE_SEGUIMIENTO
        )
    )
    avisos = leer_avisos(carpeta)
    medicion = medir(
        avisos,
        respuestas,
        criterio.puntaje_minimo,
        (criterio.descartar_despues_de_dias or 4) * 24,
        sin_buzon,
    )
    texto = parte(seguir(avisos, respuestas), medicion)
    if al_telefono:
        avisar(texto)
    return texto


async def ver_correos(criterio: Criterio) -> str:
    """Qué hay en el buzón, por tipo. Sólo lee.

    Es la herramienta que hacía falta para poder decir "no se descarta
    ninguno": antes, lo que el clasificador no reconocía desaparecía sin
    contarse, y no había forma de saber cuánto era ni de qué.
    """
    if not criterio.fuentes.get("postulaciones", True):
        return "La fuente `postulaciones` está apagada en el TOML."
    usuario = os.environ.get("GMAIL_USUARIO", "")
    clave = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not usuario or not clave:
        return "Faltan GMAIL_USUARIO / GMAIL_APP_PASSWORD: sin buzón no hay nada que mirar."
    respuestas = await asyncio.to_thread(
        fuentes.respuestas_por_imap, usuario, clave, DIAS_DE_SEGUIMIENTO
    )
    return inventario(respuestas)


def main() -> None:
    parser = argparse.ArgumentParser(description="Trae ofertas de trabajo y avisa por Telegram.")
    parser.add_argument(
        "--probar", action="store_true", help="Muestra qué devuelve cada fuente y no avisa"
    )
    parser.add_argument(
        "--agregar-empresa",
        nargs=2,
        metavar=("NOMBRE", "URL"),
        help="Resuelve la URL de una página de empleos, la prueba, e imprime el TOML",
    )
    parser.add_argument(
        "--probar-empresas",
        action="store_true",
        help="Dice cuáles tokens de [[empresas]] responden y cuántos puestos traen",
    )
    parser.add_argument(
        "--retraso",
        action="store_true",
        help="Cuánto tardás en postular después del aviso, y qué quedó sin postular",
    )
    parser.add_argument(
        "--correos",
        action="store_true",
        help="Qué hay en el buzón por tipo, incluido lo que no se reconoció",
    )
    parser.add_argument(
        "--seguimiento",
        action="store_true",
        help="En qué quedó cada postulación: entrevista, piden algo, esperando, cerrada",
    )
    parser.add_argument(
        "--al-telefono",
        action="store_true",
        help="Con --retraso o --seguimiento: manda el parte al panel además de imprimirlo",
    )
    parser.add_argument(
        "--sin-avisar", action="store_true", help="Corre entero pero no manda el mensaje"
    )
    parser.add_argument(
        "--minimo", type=int, default=None, help="Puntaje mínimo para avisar (pisa el del TOML)"
    )
    parser.add_argument("--criterio", type=Path, default=CRITERIO_POR_DEFECTO)
    parser.add_argument(
        "--consulta-upwork",
        default="AI agent LangGraph RAG Next.js",
        help="Qué buscar en Upwork, si esa fuente está encendida",
    )
    args = parser.parse_args()

    criterio = cargar_criterio(args.criterio)
    if args.minimo is not None:
        criterio = replace(criterio, puntaje_minimo=args.minimo)

    if args.agregar_empresa:
        print(asyncio.run(agregar_empresa(*args.agregar_empresa)))
        return
    if args.probar_empresas:
        print(asyncio.run(probar_empresas(criterio)))
        return
    if args.al_telefono and not (args.retraso or args.seguimiento):
        # Sin esto el flag se ignoraba en silencio y arrancaba una vuelta
        # completa: escribía memoria y mandaba el aviso de ofertas. Quien lo
        # tipea esperando el informe recibía otra cosa, y encima con efectos.
        parser.error("--al-telefono necesita --retraso o --seguimiento")
    if args.correos:
        # Sólo lectura, igual que --retraso y --seguimiento.
        print(asyncio.run(ver_correos(criterio)))
        return
    if args.seguimiento:
        # Sólo lectura, igual que --retraso: se puede mirar con una vuelta en curso.
        print(asyncio.run(ver_seguimiento(criterio, carpeta_de_trabajo(), args.al_telefono)))
        return
    if args.retraso:
        # No toma el cerrojo: es de sólo lectura y tiene que poder mirarse
        # mientras una vuelta está corriendo.
        print(asyncio.run(medir_retraso(criterio, carpeta_de_trabajo(), args.al_telefono)))
        return
    if args.probar:
        # `--probar` no escribe nada: no toma el turno ni molesta a la vuelta
        # que esté corriendo. Mirar qué devuelven los feeds tiene que poder
        # hacerse en cualquier momento.
        print(asyncio.run(probar(criterio, args.consulta_upwork)))
        return

    carpeta = carpeta_de_trabajo()
    try:
        with turno(carpeta):
            print(
                asyncio.run(
                    una_vuelta(
                        criterio, carpeta, args.consulta_upwork, con_aviso=not args.sin_avisar
                    )
                )
            )
    except YaCorriendo:
        # Sale en silencio y con código 0: desde el cron, "la anterior todavía
        # no terminó" es una vuelta que se saltea, no una falla que haya que
        # mirar. La siguiente sale en dos horas.
        logger.info("turno_ocupado", detail="otra vuelta en curso; esta se saltea")


if __name__ == "__main__":
    main()
