"""Las herramientas con las que el agente opera en papel.

Tres cosas y en este orden: **mirar** el mercado, **decidir** una entrada, y
**cerrar** lo que quedó abierto. El orden importa porque el registro solo vale
si la razón se escribe antes de saber el resultado, y eso se garantiza haciendo
que `abrir_operacion` exija la razón como argumento — no hay forma de registrar
una entrada sin haberla justificado.

**Los números no los pone el modelo.** Precio, indicadores, R múltiplo: todo
sale del código. Ya está medido que granite da 1.43 donde el valor real es
17.35, y una operación registrada sobre un precio inventado contamina el eje
entero. El modelo elige **cuándo y por qué**; el resto es aritmética.
"""

import os
import urllib.error
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from api.logging import get_logger
from paper.mercado import MercadoNoDisponible, indicadores, velas
from paper.publicar import publicar
from paper.registro import Contexto, Registro
from tools.base import Tool, ToolResult, wrap_untrusted

logger = get_logger("tools.paper")

# Los que se calculan siempre al mirar. No son los que use el eje: el contexto
# se guarda entero para poder preguntar después "¿fallaba más con ADX bajo?" sin
# haber decidido de antemano que el ADX importaba.
#
# Los tres últimos contestan una pregunta distinta de los cinco primeros: no
# "¿cómo está el mercado?" —que se responde con un número— sino "¿a qué PRECIO
# pasa algo?". Sin ellos el modelo solo puede razonar sobre el piso y el techo
# de las últimas 20 velas, que es aritmética sobre las velas y no estructura; y
# sin un nivel concreto, `dejar_orden` no tiene dónde ponerse.
#
# Medido el 2026-09-13, sesión de qwen3.6:27b: en cinco vueltas describió "rango
# estrecho, precio cerca del tope" —el escenario exacto de una orden límite— y
# no dejó ni una.
SIEMPRE = ["atr", "adx", "rsi", "macd", "ema", "liquidity", "fvg", "regime", "divergencias"]

# El par del experimento. Ver la cabecera de paper/sesion.py: con un solo par la
# diferencia entre operaciones es la hipótesis y no el activo.
SIMBOLO_UNICO = "BTCUSDT"


class MirarArgs(BaseModel):
    simbolo: str = Field(default="BTCUSDT", description="Par, por ejemplo BTCUSDT o ETHUSDT")
    intervalo: str = Field(
        default="",
        description=(
            "Dejalo VACÍO (lo normal) y recibís el mapa: 15m, 1h y 4h del mismo instante, "
            "compactos, cada uno con su rango, su ATR y su régimen. Poné 15m, 1h o 4h solo "
            "si necesitás UN gráfico con todo el detalle."
        ),
    )


def _contexto_de(datos: dict[str, Any], ind: dict[str, Any]) -> Contexto:
    """El estado del gráfico, listo para sellar con la razón."""
    ultimas = datos["velas"]
    ahora = ultimas[-1]
    momento = datetime.fromtimestamp(ahora["time"], UTC)
    # El rango de las últimas 20 velas: es lo que RANGE-SWEEP mira, y sale de
    # aritmética sobre las velas, no de un indicador.
    ventana = ultimas[-20:]
    techo = max(v["high"] for v in ventana)
    piso = min(v["low"] for v in ventana)
    ancho_pct = (techo - piso) / piso * 100 if piso else None
    # Volumen contra la media: un barrido con volumen normal y uno con el triple
    # no son la misma señal.
    medias = [v["volume"] for v in ultimas[-50:-1]]
    promedio = sum(medias) / len(medias) if medias else 0
    return Contexto(
        precio=ahora["close"],
        timestamp=momento.isoformat(),
        dia_semana=momento.weekday(),
        hora_utc=momento.hour,
        ancho_rango_pct=round(ancho_pct, 3) if ancho_pct is not None else None,
        velas_en_rango=len(ventana),
        volumen_relativo=round(ahora["volume"] / promedio, 2) if promedio else None,
        extra={
            "fuente": datos.get("fuente"),
            "rango_techo": techo,
            "rango_piso": piso,
            "indicadores": ind,
        },
    )


def _mirar(args: MirarArgs, max_chars: int) -> ToolResult:
    try:
        datos = velas(args.simbolo, args.intervalo, 200)
    except MercadoNoDisponible as exc:
        return ToolResult(content=str(exc), summary={"error": "sin datos"}, ok=False)

    ind = indicadores(datos["velas"], SIEMPRE)
    ctx = _contexto_de(datos, ind)
    adx = ind.get("adx") or {}
    macd = ind.get("macd") or {}

    lineas = [
        f"{args.simbolo} {args.intervalo} — {datos['fuente']}",
        f"precio {ctx.precio}",
        f"rango de 20 velas: {ctx.extra['rango_piso']} a {ctx.extra['rango_techo']} "
        f"({ctx.ancho_rango_pct}% de ancho)",
        # ⚠ EL NÚMERO HECHO, NO EL CÁLCULO. Trampa 2 de paper/TRAMPAS.md: el
        # modelo dijo «en el techo» con el precio un 1,2 % por debajo, y «en el
        # piso» con el precio al 73 % del recorrido. Con el porcentaje delante
        # no hay nada que estimar.
        f"al {_posicion_en_rango(ctx):.0f}% del rango: 0% es el piso, 100% el techo",
        f"volumen relativo a las últimas 50: {ctx.volumen_relativo}x",
        f"día {ctx.dia_semana} (0=lunes), hora {ctx.hora_utc} UTC",
        "",
        f"ATR {ind.get('atr')}",
        f"ADX {adx.get('adx')} (+DI {adx.get('plusDI')} / -DI {adx.get('minusDI')})",
        f"RSI {ind.get('rsi')}",
        f"MACD {macd.get('macd')} sobre señal {macd.get('signal')}",
        # ⚠ LA POSICIÓN RELATIVA, DICHA. Trampa 8 de paper/TRAMPAS.md: «la
        # EMA20 es 77698.89, por encima del precio» con el precio en 79136, dos
        # veces en una vuelta, y de ahí «posible reversión». Dos números sueltos
        # los ordena al revés; con la palabra delante no hay nada que ordenar.
        f"EMA20 {ind.get('ema')}{_respecto_a_ema(ctx.precio, ind.get('ema'))}",
    ]
    # ── Los niveles, aparte de los números ────────────────────────────────────
    #
    # Van en su propio bloque y con su etiqueta porque son de otra clase: un RSI
    # de 57 describe el mercado, un pool en 76.471 es un SITIO al que se puede
    # mandar una orden. Se pintan como texto y no como JSON para que compitan
    # menos con el resto del contexto — ya está medido que el modelo elige peor
    # cuanto más ruido tiene delante.
    regimen = ind.get("regime") or {}
    if regimen.get("regimen"):
        lineas.append("")
        lineas.append(
            f"régimen: {regimen['regimen']} (chop {regimen.get('chop')}, "
            f"ancho de bandas en el percentil {regimen.get('bbw_percentil')})"
        )

    pools = ind.get("liquidity") or []
    if pools:
        lineas.append("")
        lineas.append("pools de liquidez sin barrer — ahí están los stops:")
        for p in pools:
            # `swings` > 1 es un EQH/EQL: varios máximos o mínimos al mismo nivel
            # concentran más stops que un pivote suelto.
            iguales = f", {p['swings']} swings al mismo nivel" if p.get("swings", 1) > 1 else ""
            lineas.append(
                f"  {p['precio']} ({p['lado']} del precio, fuerza {p['fuerza']}{iguales})"
            )

    gaps = ind.get("fvg") or []
    if gaps:
        lineas.append("")
        lineas.append("huecos sin negociar (FVG) todavía sin rellenar:")
        for g in gaps:
            # Un FVG roto cambia de signo: deja de sostener y pasa a resistir, o
            # al revés. No se deduce del precio, hay que decirlo.
            vuelto = " — INVERTIDO, ahora funciona al revés" if g.get("invertido") else ""
            lineas.append(f"  {g['piso']} a {g['techo']} ({g['tipo']}{vuelto})")

    # ⚠ DIVERGENCIAS DE AGOTAMIENTO, NO DE OSCILADOR. No comparan el precio con
    # el RSI: comparan la FUERZA de un swing contra la del anterior —cuánto
    # rango recorrió y en cuántas velas—. Un nuevo extremo con menos impulso que
    # el previo es un movimiento agotándose.
    #
    # Se dice "hace N velas" y no la hora: lo que decide si todavía cuenta es
    # cuánto gráfico ha pasado encima, no el reloj.
    divs = ind.get("divergencias") or []
    if divs:
        lineas.append("")
        lineas.append("agotamiento del impulso (el swing nuevo llegó con menos fuerza):")
        for d in divs:
            lineas.append(f"  {d['precio']} ({d['tipo']}, hace {d['hace_velas']} velas)")

    if not datos["velas"][-1].get("takerBuyVolume"):
        lineas.append("")
        lineas.append("(sin CVD: esta fuente no expone el volumen comprador)")

    return ToolResult(
        content=wrap_untrusted(f"MERCADO {args.simbolo}", "\n".join(lineas), max_chars),
        summary={"simbolo": args.simbolo, "precio": ctx.precio, "fuente": datos["fuente"]},
    )


class AbrirArgs(BaseModel):
    eje: str = Field(description="Qué hipótesis genera esta entrada, por ejemplo 'range-sweep'")
    simbolo: str = Field(default="BTCUSDT", description="Par")
    intervalo: str = Field(default="15m", description="Temporalidad")
    direccion: str = Field(description="long o short")
    stop_loss: float = Field(description="Precio del stop. En un long va debajo del precio")
    take_profit: float = Field(default=0.0, description="Precio objetivo. 0 si no hay")
    razon: str = Field(
        description=(
            "Por qué entrás ACÁ y no cinco velas después. Concreto: qué viste en el "
            "gráfico. Esto queda sellado y es lo que se va a revisar cuando la "
            "operación cierre."
        )
    )


def _abrir(registro: Registro, args: AbrirArgs) -> ToolResult:
    """Registra una entrada. El precio y el contexto salen del mercado, ahora."""
    try:
        datos = velas(args.simbolo, args.intervalo, 200)
    except MercadoNoDisponible as exc:
        return ToolResult(content=str(exc), summary={"error": "sin datos"}, ok=False)

    ctx = _contexto_de(datos, indicadores(datos["velas"], SIEMPRE))
    try:
        oid = registro.abrir(
            eje=args.eje,
            simbolo=args.simbolo,
            direccion=args.direccion,
            contexto=ctx,
            razon=args.razon,
            stop_loss=args.stop_loss,
            take_profit=args.take_profit or None,
        )
    except ValueError as exc:
        return ToolResult(content=str(exc), summary={"error": "rechazada"}, ok=False)

    riesgo = abs(ctx.precio - args.stop_loss)
    logger.info("paper_abierta", id=oid, eje=args.eje, simbolo=args.simbolo)
    return ToolResult(
        content=(
            f"Operación #{oid} registrada: {args.direccion} {args.simbolo} a {ctx.precio}, "
            f"stop {args.stop_loss} (riesgo {riesgo:.2f}). Eje '{args.eje}'.\n"
            f"La razón quedó sellada y no se puede cambiar."
        ),
        summary={"id": oid, "eje": args.eje, "precio": ctx.precio},
    )


class CerrarArgs(BaseModel):
    operacion_id: int = Field(description="El número que devolvió abrir_operacion")
    motivo: str = Field(
        description=(
            "stop, objetivo o manual. Cierra TODO lo que quede de la posición; "
            "para soltar solo una parte usá salir_parcial."
        )
    )
    analisis: str = Field(
        default="",
        description="Qué pasó, ahora que se conoce el resultado. Va en un campo aparte.",
    )


def _cerrar(registro: Registro, args: CerrarArgs) -> ToolResult:
    abiertas = {o["id"]: o for o in registro.abiertas()}
    operacion = abiertas.get(args.operacion_id)
    if operacion is None:
        return ToolResult(
            content=(
                f"la operación {args.operacion_id} no está abierta. Abiertas: {sorted(abiertas)}"
            ),
            summary={"error": "no existe"},
            ok=False,
        )
    try:
        datos = velas(operacion["simbolo"], "15m", 60)
    except MercadoNoDisponible as exc:
        return ToolResult(content=str(exc), summary={"error": "sin datos"}, ok=False)

    precio = datos["velas"][-1]["close"]
    # ⚠ EL ValueError SE CAPTURA, y no es simetría con `_abrir` por gusto. Entre
    # el `abiertas()` de arriba y esta línea hay una llamada a Node que puede
    # tardar hasta 45 s, y el agente admite dos runs a la vez sobre el MISMO
    # Registro: en esa ventana la operación puede haberse cerrado por el otro
    # lado. Sin capturar, el modelo recibía un traceback en vez del texto que le
    # dice qué pasó y qué sigue abierto.
    #
    # También cubre el motivo inválido, que `cerrar()` ahora rechaza.
    try:
        r = registro.cerrar(
            args.operacion_id,
            precio_salida=precio,
            motivo=args.motivo,
            contexto_salida=_contexto_de(datos, indicadores(datos["velas"], SIEMPRE)),
            analisis=args.analisis,
        )
    except ValueError as exc:
        return ToolResult(
            content=f"no se pudo cerrar la operación {args.operacion_id}: {exc}",
            summary={"error": "no se cerró"},
            ok=False,
        )
    logger.info("paper_cerrada", id=args.operacion_id, r=round(r, 3))
    return ToolResult(
        content=(
            f"Operación #{args.operacion_id} cerrada a {precio}: {r:+.2f}R ({args.motivo}).\n"
            f"Su razón de entrada fue: «{operacion['razon']}»"
        ),
        summary={"id": args.operacion_id, "r": round(r, 3)},
    )


class EstadoArgs(BaseModel):
    pass


class ParcialArgs(BaseModel):
    operacion_id: int = Field(description="El número que devolvió abrir_operacion")
    fraccion: float = Field(
        description=(
            "Qué parte de la posición ORIGINAL se suelta, entre 0 y 1. 0.5 es la mitad. "
            "Siempre referido al total, no a lo que queda."
        )
    )
    analisis: str = Field(
        default="", description="Por qué se toma acá el parcial. Va en un campo aparte."
    )


def _parcial(registro: Registro, args: ParcialArgs) -> ToolResult:
    abiertas = {o["id"]: o for o in registro.abiertas()}
    operacion = abiertas.get(args.operacion_id)
    if operacion is None:
        return ToolResult(
            content=(
                f"la operación {args.operacion_id} no está abierta. Abiertas: {sorted(abiertas)}"
            ),
            summary={"error": "no existe"},
            ok=False,
        )
    try:
        datos = velas(operacion["simbolo"], "15m", 60)
    except MercadoNoDisponible as exc:
        return ToolResult(content=str(exc), summary={"error": "sin datos"}, ok=False)

    precio = datos["velas"][-1]["close"]
    try:
        r = registro.salir_parcial(
            args.operacion_id,
            precio_salida=precio,
            fraccion=args.fraccion,
            contexto_salida=_contexto_de(datos, indicadores(datos["velas"], SIEMPRE)),
            analisis=args.analisis,
        )
    except ValueError as exc:
        return ToolResult(
            content=f"no se pudo tomar el parcial: {exc}",
            summary={"error": "no se tomó"},
            ok=False,
        )
    logger.info("paper_parcial", id=args.operacion_id, fraccion=args.fraccion, r=round(r, 3))
    return ToolResult(
        content=(
            f"Soltaste el {args.fraccion:.0%} de la operación {args.operacion_id} a {precio}: "
            f"{r:+.2f}R en ese tramo. El resto sigue abierto — el R de la operación entera "
            f"se calcula al cerrarla, ponderando cada tramo por lo que soltó."
        ),
        summary={"id": args.operacion_id, "fraccion": args.fraccion, "r_tramo": round(r, 3)},
    )


class PredecirArgs(BaseModel):
    nivel: float = Field(
        description=(
            "El precio concreto de la apuesta. Para 'arriba' tiene que estar POR ENCIMA "
            "del precio actual, y para 'abajo' por debajo: al revés ya ocurrió."
        )
    )
    hacia: str = Field(description="arriba o abajo")
    probabilidad: float = Field(
        description=(
            "De 0 a 1: qué probabilidad le das a que el precio TOQUE ese nivel antes de "
            "que venza. Se puntúa con Brier —(probabilidad − ocurrió)²—, así que decir "
            "0.9 y fallar cuesta mucho más que decir 0.6 y fallar. Si no sabés, 0.5 es "
            "la respuesta honesta y no se penaliza más que cualquier otra."
        )
    )
    razonamiento: str = Field(
        description="Qué ves que justifica ESA probabilidad. Queda sellado al predecir."
    )
    temporalidad: str = Field(
        default="15m",
        description=(
            "En qué gráfico lo estás viendo: 15m, 1h o 4h. Tiene que ser el mismo que "
            "miraste, porque el contexto se sella con él."
        ),
    )
    regimen: str = Field(
        default="",
        description="Qué régimen ves vos: RANGE, TREND o NEUTRAL. Opcional.",
    )
    horas_vigencia: float = Field(
        default=0.0,
        description=(
            "Cuántas horas tiene el precio para tocar el nivel. Dejalo en 0 y se "
            "calcula desde la temporalidad: 6h en 15m, 24h en 1h, 96h en 4h. Podés "
            "acortarlo si tu tesis pide menos tiempo; NUNCA alargarlo: el plazo máximo "
            "es el de su marco, y un 15m a 96h es una tesis de 4h con otro nombre."
        ),
    )


def _pista_de_marco_menor(registro: Registro, marco: str, nivel: float, hacia: str) -> str:
    """Al rechazar en un marco grande, decir CON NÚMEROS qué pasaría en 15m.

    ⚠ EL MODELO SIGUE A LA HERRAMIENTA, NO AL PROMPT. Medido el 2026-09-14 con
    `qwen3:14b` pensando: ante cada rechazo cita los números del mensaje y
    calcula con ellos —«750.21 < 976.5, that's why it was flagged»—, y aun así
    en cuatro intentos seguidos movió el NIVEL y nunca el MARCO, con el prompt
    diciéndole en mayúsculas que bajara a 15m. Su primer intento, 79237.42,
    entraba en 15m de sobra. La pista va donde él mira: en el rechazo, y con
    cifras que pueda verificar, porque es lo que hace con todo lo demás.

    Si el nivel tampoco entra en 15m se dice igual: mandarlo a un marco donde
    volvería a chocar sería gastar otra iteración por nada.
    """
    if marco.strip() == "15m":
        return ""
    try:
        datos = velas(SIMBOLO_UNICO, "15m", 200)
    except MercadoNoDisponible:
        return ""
    ctx = _contexto_de(datos, indicadores(datos["velas"], ["atr"]))
    atr = ctx.extra.get("indicadores", {}).get("atr")
    if not isinstance(atr, int | float) or atr <= 0:
        return ""
    minimo = atr * registro.MARGEN_ATR
    dista = abs(nivel - ctx.precio)
    if dista < minimo:
        return (
            f"\nEn 15m tampoco: el ATR es {atr:.0f}, el mínimo {minimo:.0f}, y {nivel} dista "
            f"{dista:.0f} del precio {ctx.precio}. Buscá otro nivel, no otro marco."
        )
    choca = [
        v
        for v in registro.predicciones_vivas()
        if str(v.get("temporalidad") or "") == "15m"
        and v["hacia"] == hacia
        and abs(nivel - v["nivel"]) < minimo
    ]
    if choca:
        return (
            f"\nEn 15m el nivel {nivel} chocaría con la predicción viva #{choca[0]['id']} "
            f"({choca[0]['nivel']}, {hacia}). Otro nivel en 15m sí entra."
        )
    return (
        f"\nEn 15m SÍ entra: el ATR es {atr:.0f}, el mínimo {minimo:.0f}, y {nivel} dista "
        f"{dista:.0f} del precio {ctx.precio}. Las predicciones vivas en {marco.strip()} no "
        f'bloquean 15m. Repetí la llamada con temporalidad="15m" y horas_vigencia=0: '
        f"vence en {registro.PLAZO_POR_MARCO['15m']:.0f} h, que es OTRA pregunta, no la "
        f"misma apuesta más corta."
    )


def _predecir(registro: Registro, args: PredecirArgs, max_chars: int) -> ToolResult:
    # ⚠ LAS VELAS SON LAS DE SU TEMPORALIDAD, no siempre 15m. El contexto se
    # sella con la predicción, así que pedir 15m mientras el modelo razona sobre
    # un gráfico de 4h guardaría un ATR y un rango que no son los que miró — y la
    # distancia mínima entre niveles se calcula justo con ese ATR.
    try:
        datos = velas(SIMBOLO_UNICO, args.temporalidad, 200)
    except MercadoNoDisponible as exc:
        return ToolResult(content=str(exc), summary={"error": "sin datos"}, ok=False)

    ctx = _contexto_de(datos, indicadores(datos["velas"], SIEMPRE))
    try:
        pid = registro.predecir(
            simbolo=SIMBOLO_UNICO,
            contexto=ctx,
            nivel=args.nivel,
            hacia=args.hacia,
            probabilidad=args.probabilidad,
            razonamiento=args.razonamiento,
            horas_vigencia=args.horas_vigencia,
            regimen_dicho=args.regimen,
            temporalidad=args.temporalidad,
        )
    except ValueError as exc:
        return ToolResult(
            content=f"no se registró la predicción: {exc}"
            + _pista_de_marco_menor(registro, args.temporalidad, args.nivel, args.hacia),
            summary={"error": "no se registró"},
            ok=False,
        )

    logger.info("paper_prediccion", id=pid, nivel=args.nivel, p=args.probabilidad)
    # El plazo que de verdad se guardó: con `horas_vigencia` en 0 lo derivó el
    # registro desde la temporalidad, y decir "en 0h" sería mentirle al modelo
    # sobre la apuesta que acaba de hacer.
    plazo = args.horas_vigencia or Registro.PLAZO_POR_MARCO.get(args.temporalidad, 24.0)
    return ToolResult(
        content=wrap_untrusted(
            "PREDICCIÓN",
            f"Registrada #{pid}: {args.probabilidad:.0%} de que {SIMBOLO_UNICO} toque "
            f"{args.nivel} hacia {args.hacia} en {plazo:g}h "
            f"(precio ahora {ctx.precio}).\n"
            "La resuelve el código contra las velas, no vos. Tu razonamiento queda sellado.",
            max_chars,
        ),
        summary={"id": pid, "nivel": args.nivel, "probabilidad": args.probabilidad},
    )


class OrdenArgs(BaseModel):
    eje: str = Field(description="Qué hipótesis la genera")
    direccion: str = Field(description="long o short")
    precio_limite: float = Field(
        description=(
            "A qué precio entrar. Para un LONG va por DEBAJO del precio actual "
            "—esperás a que baje— y para un SHORT por encima."
        )
    )
    stop_loss: float = Field(description="Stop, del lado correcto del precio límite")
    take_profit: float | None = Field(default=None, description="Objetivo, opcional")
    razon: str = Field(
        description=(
            "Qué ves que justifica entrar a ESE precio y no al de ahora. Queda sellada "
            "al dejar la orden, no al dispararse."
        )
    )
    horas_vigencia: float = Field(
        default=24.0,
        description="Cuántas horas sigue viva. Pasadas, se marca vencida y no entra.",
    )


def _dejar_orden(registro: Registro, args: OrdenArgs, max_chars: int) -> ToolResult:
    try:
        datos = velas(SIMBOLO_UNICO, "15m", 200)
    except MercadoNoDisponible as exc:
        return ToolResult(content=str(exc), summary={"error": "sin datos"}, ok=False)
    ctx = _contexto_de(datos, indicadores(datos["velas"], SIEMPRE))
    try:
        oid = registro.dejar_orden(
            eje=args.eje,
            simbolo=SIMBOLO_UNICO,
            direccion=args.direccion,
            contexto=ctx,
            razon=args.razon,
            precio_limite=args.precio_limite,
            stop_loss=args.stop_loss,
            take_profit=args.take_profit,
            horas_vigencia=args.horas_vigencia,
        )
    except ValueError as exc:
        return ToolResult(
            content=f"no se pudo dejar la orden: {exc}",
            summary={"error": "invalida"},
            ok=False,
        )
    distancia = abs(ctx.precio - args.precio_limite) / ctx.precio * 100
    return ToolResult(
        content=(
            f"Orden #{oid} dejada: {args.direccion} {SIMBOLO_UNICO} a {args.precio_limite} "
            f"(el precio está en {ctx.precio}, {distancia:.2f}% de distancia), stop "
            f"{args.stop_loss}. Vive {args.horas_vigencia}h. Si el precio la toca mientras "
            f"no estás, entra sola y la vas a ver abierta la próxima vez."
        ),
        summary={"id": oid, "limite": args.precio_limite, "eje": args.eje},
    )


class CancelarOrdenArgs(BaseModel):
    orden_id: int = Field(description="El número que devolvió dejar_orden")
    nota: str = Field(default="", description="Por qué ya no tiene sentido")


def _cancelar_orden(registro: Registro, args: CancelarOrdenArgs) -> ToolResult:
    try:
        registro.cancelar_orden(args.orden_id, nota=args.nota)
    except ValueError as exc:
        return ToolResult(content=str(exc), summary={"error": "no se canceló"}, ok=False)
    return ToolResult(
        content=f"Orden #{args.orden_id} cancelada.",
        summary={"id": args.orden_id},
    )


class MoverStopArgs(BaseModel):
    operacion_id: int = Field(description="El número que devolvió abrir_operacion")
    nuevo_stop: float = Field(description="El precio del stop nuevo")
    razon: str = Field(default="", description="Por qué se mueve")


def _mover_stop(registro: Registro, args: MoverStopArgs) -> ToolResult:
    try:
        registro.mover_stop(args.operacion_id, nuevo_stop=args.nuevo_stop, razon=args.razon)
    except ValueError as exc:
        return ToolResult(
            content=f"no se pudo mover el stop: {exc}",
            summary={"error": "no se movió"},
            ok=False,
        )
    return ToolResult(
        content=(
            f"Stop de la operación {args.operacion_id} movido a {args.nuevo_stop}. "
            "El R múltiplo se sigue midiendo contra el stop ORIGINAL: es lo que hace "
            "comparables las operaciones entre sí."
        ),
        summary={"id": args.operacion_id, "stop": args.nuevo_stop},
    )


MARCOS_DEL_MAPA = ("15m", "1h", "4h")


def _n(x: Any, dec: int = 1) -> Any:
    """Redondea si es número, para que el mapa quepa; deja pasar lo demás."""
    return round(x, dec) if isinstance(x, int | float) else x


def _hechos_de_la_vela(velas: list[dict[str, Any]], atr: Any) -> str:
    """La vela en curso comparada con las 19 previas, como HECHO y no como dictamen.

    Es lo que `range-sweep` mira —una mecha que excede el rango y un cierre que
    vuelve adentro— y lo que `dip-trap` mira —una vela grande con volumen—.
    Se dice cuánto excedió y dónde está el cierre; si eso es un barrido lo
    decide el modelo. Un «range-sweep: SÍ» acá sería el script decidiendo, y
    eso es lo que `sesion.py` prohíbe: habría dos estrategias y el registro no
    diría cuál produjo cada resultado.
    """
    if len(velas) < 21 or not isinstance(atr, int | float) or atr <= 0:
        return "vela en curso: sin datos suficientes"
    v = velas[-1]
    previas = velas[-20:-1]
    techo = max(c["high"] for c in previas)
    piso = min(c["low"] for c in previas)
    rango = v["high"] - v["low"]
    partes = [f"vela en curso: rango {rango / atr:.1f} ATR"]
    if rango > 0:
        partes.append(f"cierre en el {(v['close'] - v['low']) / rango * 100:.0f}% de su rango")
    if v["high"] > techo:
        dentro = "cierre dentro" if v["close"] <= techo else "cierre FUERA"
        partes.append(
            f"mecha {v['high'] - techo:.0f} por ENCIMA del techo de las 19 previas, {dentro}"
        )
    if v["low"] < piso:
        dentro = "cierre dentro" if v["close"] >= piso else "cierre FUERA"
        partes.append(
            f"mecha {piso - v['low']:.0f} por DEBAJO del piso de las 19 previas, {dentro}"
        )
    return " · ".join(partes)


def _bloque(marco: str, datos: dict[str, Any], ind: dict[str, Any]) -> str:
    """Un marco en cuatro a siete líneas. Hechos; los mismos que `_mirar`, apretados."""
    ctx = _contexto_de(datos, ind)
    adx = ind.get("adx") or {}
    macd = ind.get("macd") or {}
    reg = ind.get("regime") or {}
    piso, techo = ctx.extra["rango_piso"], ctx.extra["rango_techo"]
    cabecera = (
        f"── {marco} ── precio {ctx.precio} · al {_posicion_en_rango(ctx):.0f}% del rango "
        f"{piso}–{techo} ({ctx.ancho_rango_pct}% de ancho)"
    )
    if reg.get("regimen"):
        cabecera += f" · régimen medido: {reg['regimen']} (chop {_n(reg.get('chop'))})"
    lineas = [
        cabecera,
        f"   ATR {_n(ind.get('atr'))} · ADX {_n(adx.get('adx'))} · RSI {_n(ind.get('rsi'))} · "
        f"MACD {_n(macd.get('macd'))} sobre señal {_n(macd.get('signal'))} · "
        f"EMA20 {_n(ind.get('ema'))}{_respecto_a_ema(ctx.precio, ind.get('ema'))}",
        f"   volumen {ctx.volumen_relativo}x de la media · "
        f"{_hechos_de_la_vela(datos['velas'], ind.get('atr'))}",
    ]
    pools = ind.get("liquidity") or []
    if pools:
        lineas.append(
            "   pools sin barrer: "
            + ", ".join(
                f"{p['precio']} ({p['lado']}, f{p['fuerza']}"
                + (f", {p['swings']} swings" if p.get("swings", 1) > 1 else "")
                + ")"
                for p in pools[:6]
            )
        )
    gaps = ind.get("fvg") or []
    if gaps:
        lineas.append(
            "   FVG sin rellenar: "
            + ", ".join(
                f"{g['piso']}–{g['techo']} "
                f"({g['tipo']}{', INVERTIDO' if g.get('invertido') else ''})"
                for g in gaps[:4]
            )
        )
    divs = ind.get("divergencias") or []
    if divs:
        lineas.append(
            "   agotamiento: "
            + ", ".join(
                f"{d['precio']} ({d['tipo']}, hace {d['hace_velas']} velas)" for d in divs[:3]
            )
        )
    return "\n".join(lineas)


def _mapa(simbolo: str, max_chars: int) -> ToolResult:
    """Los tres gráficos del mismo instante, compactos.

    ⚠ POR QUÉ EXISTE. Medido el 2026-09-14 con qwen3:14b pensando: pedía 4h,
    luego 15m, y entre medias leía el rango de 4h dentro del gráfico de 15m
    —régimen dicho RANGE, medido TREND, tres veces de cuatro—. Y cada llamada
    a `mirar_mercado` son ~7 minutos de pensamiento y una de las seis
    iteraciones. Con el mapa, una llamada trae los tres y la cabecera dice lo
    que la trampa 6 de TRAMPAS.md enseñó: cada marco tiene lo suyo.

    Hechos, no veredictos: ver `_hechos_de_la_vela`.
    """
    bloques: list[str] = []
    fallos: list[str] = []
    precio: float | None = None
    fuente = ""
    sin_cvd = False
    for marco in MARCOS_DEL_MAPA:
        try:
            datos = velas(simbolo, marco, 200)
        except MercadoNoDisponible as exc:
            fallos.append(f"{marco}: {exc}")
            continue
        ind = indicadores(datos["velas"], SIEMPRE)
        bloques.append(_bloque(marco, datos, ind))
        ultima = datos["velas"][-1]
        precio, fuente = ultima["close"], datos["fuente"]
        sin_cvd = sin_cvd or not ultima.get("takerBuyVolume")
    if not bloques:
        return ToolResult(content="; ".join(fallos), summary={"error": "sin datos"}, ok=False)
    cabecera = (
        f"{simbolo} — los tres gráficos del mismo instante ({fuente}). Cada marco tiene SU "
        "rango, SU ATR y SU régimen: no mezcles los de uno con los de otro. El % del rango "
        "y la EMA son de ese marco; una predicción en 15m se mide con el ATR de 15m."
    )
    cuerpo = cabecera + "\n\n" + "\n\n".join(bloques)
    if fallos:
        cuerpo += "\n\n(sin " + "; ".join(fallos) + ")"
    if sin_cvd:
        cuerpo += "\n\n(sin CVD: esta fuente no expone el volumen comprador)"
    return ToolResult(
        content=wrap_untrusted(f"MERCADO {simbolo}", cuerpo, max_chars),
        summary={"simbolo": simbolo, "precio": precio, "marcos": [b[3:6].strip() for b in bloques]},
    )


def _respecto_a_ema(precio: float, ema: Any) -> str:
    """« (precio 1.9% por ENCIMA de la EMA20)», o nada si no hay EMA."""
    if not isinstance(ema, int | float) or ema <= 0:
        return ""
    pct = (precio - ema) / ema * 100
    lado = "por ENCIMA de" if pct > 0 else "por DEBAJO de" if pct < 0 else "justo en"
    return f" (precio {abs(pct):.1f}% {lado} la EMA20)"


def _posicion_en_rango(ctx: Contexto) -> float:
    """Dónde está el precio dentro del rango de 20 velas, de 0 (piso) a 100 (techo)."""
    piso, techo = ctx.extra["rango_piso"], ctx.extra["rango_techo"]
    ancho = techo - piso
    if not ancho:
        return 50.0
    return (ctx.precio - piso) / ancho * 100


def _precio_ahora() -> float | None:
    """El último cierre de 15m, o None si el mercado no responde."""
    try:
        datos = velas(SIMBOLO_UNICO, "15m", 2)
    except MercadoNoDisponible:
        return None
    ultimas = datos.get("velas") or []
    return float(ultimas[-1]["close"]) if ultimas else None


def _como_va(o: dict[str, Any], precio: float | None) -> str:
    """« · +0.06R a favor (precio 78803.28)», o nada si no hay precio."""
    if precio is None:
        return ""
    riesgo = abs(o["precio_entrada"] - o["stop_loss"])
    if not riesgo:
        return ""
    r = (precio - o["precio_entrada"]) / riesgo
    if o["direccion"] == "short":
        r = -r
    palabra = "a favor" if r > 0 else "en contra" if r < 0 else "en tablas"
    return f" · {r:+.2f}R {palabra} (precio {precio})"


def _estado(registro: Registro) -> ToolResult:
    """Lo que quedó abierto y cómo va cada eje. Es lo que el agente lee al volver.

    Entre una sesión y la siguiente el proceso muere, así que esto es lo único
    que sabe qué había a medias.
    """
    abiertas = registro.abiertas()
    ejes = registro.por_eje()
    # ⚠ EL R ABIERTO SE LE DA HECHO, CON SIGNO Y PALABRA. Trampa 1 de
    # paper/TRAMPAS.md: el modelo cerró un short en +0,057R diciendo «1000+
    # puntos por debajo del entry […] la operación está en pérdida». Eran 57
    # puntos y un short con el precio abajo GANA. No es un filtro —no decide
    # nada—: es el mismo gesto que darle el ATR en vez de las 200 velas.
    # Best-effort: sin precio, la lista sale como antes.
    precio_ahora = _precio_ahora() if abiertas else None

    partes = []
    if abiertas:
        partes.append("Operaciones abiertas:")
        for o in abiertas:
            como_va = _como_va(o, precio_ahora)
            partes.append(
                f"  #{o['id']} {o['direccion']} {o['simbolo']} a {o['precio_entrada']} "
                f"(stop {o['stop_loss']}){como_va} · eje '{o['eje']}' · «{o['razon'][:90]}»"
            )
    else:
        partes.append("No hay operaciones abiertas.")

    if ejes:
        partes += ["", "Cómo va cada eje:"]
        for e in ejes:
            partes.append(
                f"  {e['eje']}: {e['cerradas']} cerradas, R total {e['r_total']}, "
                f"R promedio {e['r_promedio']}, aciertos {e['aciertos_pct']}%"
            )
        partes += [
            "",
            "No elijas el mejor eje mirando esta tabla: todos corren en paralelo y "
            "ninguno se ajusta hasta el final (paper/CRITERIO_ABORTO.md).",
        ]

        # ⚠ EL FRENO DEL PUNTO 4, Y VA DIRIGIDO A QUIEN LEE, NO AL AGENTE. Un
        # umbral que nadie mira no frena nada, así que tiene que aparecer donde
        # se mira; pero decirle al modelo «vas perdiendo, tené cuidado» le
        # cambiaría el comportamiento a mitad del experimento, que es
        # exactamente lo que el criterio prohíbe. Por eso informa sin instruir:
        # ni «operá menos», ni «cambiá de eje».
        #
        # No es «va perdiendo»: perder no aborta nada (ver la sección de abajo
        # del criterio). Es que una racha destructiva consume las 100
        # operaciones sin producir variedad de razones, y cien entradas
        # idénticas perdiendo no son cien datos, son uno.
        total = sum(e["r_total"] or 0 for e in ejes)
        if total <= -30:
            partes += [
                "",
                f"⚠ FRENO: {total:.1f}R acumulados, por debajo de −30R. El criterio "
                "pide parar a revisar antes de gastar más muestra. No es un veredicto "
                "sobre las hipótesis ni algo que tengas que corregir vos.",
            ]

    # Las órdenes que esperan. Sin esto el agente no sabría que dejó un plan
    # puesto y podría dejar otro encima sobre el mismo nivel.
    ordenes = registro.ordenes_vivas()
    if ordenes:
        partes += ["", "Órdenes límite esperando:"]
        for o in ordenes:
            partes.append(
                f"  #{o['id']} {o['direccion']} {o['simbolo']} a {o['precio_limite']} "
                f"(stop {o['stop_loss']}) · eje '{o['eje']}' · vence {o['vence_en'][:16]} "
                f"· «{o['razon'][:70]}»"
            )

    # Las predicciones que siguen esperando. Sin esto el agente no sabría que ya
    # apostó a un nivel y repetiría la misma apuesta cada vuelta, inflando la
    # muestra con copias en vez de con lecturas nuevas.
    vivas = registro.predicciones_vivas()
    if vivas:
        partes += ["", "Predicciones esperando resolución:"]
        for p in vivas:
            marco = f" en {p['temporalidad']}" if p.get("temporalidad") else ""
            partes.append(
                f"  #{p['id']} {p['probabilidad']:.0%} de tocar {p['nivel']} "
                f"hacia {p['hacia']}{marco} · vence {p['vence_en'][:16]}"
            )
        # ⚠ LOS NIVELES OCUPADOS, ANTES DE QUE LO INTENTE. El rechazo por
        # cercanía ya le explica el motivo, pero llega DESPUÉS de gastar una
        # iteración. Medido el 2026-09-14: cuatro llamadas seguidas rechazadas
        # —dos predicciones y dos órdenes— y la vuelta terminada en el tope sin
        # registrar nada. Saberlo antes convierte cuatro intentos ciegos en uno
        # informado.
        partes.append(
            "  (no apuntes a esos niveles ni a menos de ~1.5 ATR de ellos en su "
            "mismo marco: sería la misma apuesta dos veces. Otro marco sí vale.)"
        )

    # ⚠ LOS TRAMOS SE CALLAN POR DEBAJO DE 50 RESUELTAS, y `brier_por_tramo` lo
    # garantiza: enseñarle su tasa con 20 es invitarlo a ajustar contra ruido.
    # Ver paper/CRITERIO_PREDICCIONES.md.
    brier = registro.brier_por_tramo()
    if brier.get("tramos"):
        partes += [
            "",
            f"Tus predicciones ({brier['resueltas']} resueltas, "
            f"Brier medio {brier['brier_medio']}, tasa base {brier['tasa_base']}):",
        ]
        for t in brier["tramos"]:
            partes.append(
                f"  cuando dijiste ~{t['dijo']:.0%} (n={t['n']}), ocurrió el {t['ocurrio']:.0%}"
            )
        partes.append("Esto no es para elegir qué predecir: es para calibrar el número que decís.")
    elif brier.get("resueltas"):
        partes += [
            "",
            f"Predicciones resueltas: {brier['resueltas']}. Faltan {brier['faltan']} "
            "para que la calibración signifique algo.",
        ]

    rotos = registro.verificar_sellos()
    if rotos:
        partes += [
            "",
            f"⚠ SELLOS ROTOS en {rotos}: esas razones se editaron después de "
            "registrarse y sus operaciones no sirven.",
        ]

    return ToolResult(
        content="\n".join(partes),
        summary={"abiertas": len(abiertas), "ejes": len(ejes), "ordenes": len(ordenes)},
    )


def _publicar(registro: Registro) -> ToolResult:
    """Empuja el historial al panel web para que se pueda mirar desde fuera.

    El registro vive en SQLite dentro de la máquina donde corre el agente, y esa
    máquina se apaga. Sin esto, el historial solo existe mientras la sesión dura
    y nadie puede revisarlo después.
    """
    if not os.environ.get("PANEL_URL"):
        return ToolResult(
            content=(
                "No hay panel configurado (falta PANEL_URL), así que el historial "
                "se queda solo en este equipo. No es un error: el registro sigue "
                "completo en la base."
            ),
            summary={"publicado": False},
        )
    try:
        foto = publicar(registro)
    except (OSError, urllib.error.URLError) as e:
        # Que no se pueda publicar NO invalida la sesión: las operaciones ya
        # están registradas y selladas. Se avisa y se sigue.
        return ToolResult(
            content=(
                f"No se pudo publicar en el panel: {e}. Las operaciones están "
                "guardadas igual; se publicarán en el próximo intento."
            ),
            summary={"publicado": False},
        )
    return ToolResult(
        content=(
            f"Historial publicado: {len(foto['cerradas'])} operaciones cerradas y "
            f"{len(foto['abiertas'])} abiertas."
            + (f" ⚠ Sellos rotos en {foto['sellosRotos']}." if foto["sellosRotos"] else "")
        ),
        summary={
            "publicado": True,
            "cerradas": len(foto["cerradas"]),
            "abiertas": len(foto["abiertas"]),
        },
    )


def build_paper_tools(ruta_db: str, max_chars: int, modelo: str = "") -> list[Tool]:
    """Las herramientas de paper trading, sobre un registro concreto.

    `modelo` queda grabado en todo lo que se registre en esta sesión. Desde que
    hay dos máquinas —el Codespace con qwen3.6:27b y la Mac con un 14B— sin él
    no se podría saber si una racha mala fue el mercado o el modelo más chico.
    """
    registro = Registro(ruta_db, modelo=modelo)

    async def mirar(args: BaseModel) -> ToolResult:
        # Sin intervalo: el mapa —los tres gráficos del mismo instante—. Con
        # intervalo: un gráfico con todo el detalle. Ver `_mapa`.
        if not str(getattr(args, "intervalo", "")).strip():
            return _mapa(str(getattr(args, "simbolo", SIMBOLO_UNICO)), max_chars)
        return _mirar(args, max_chars)  # type: ignore[arg-type]

    async def abrir(args: BaseModel) -> ToolResult:
        return _abrir(registro, args)  # type: ignore[arg-type]

    async def cerrar(args: BaseModel) -> ToolResult:
        return _cerrar(registro, args)  # type: ignore[arg-type]

    async def estado(_args: BaseModel) -> ToolResult:
        return _estado(registro)

    async def publicar_historial(_args: BaseModel) -> ToolResult:
        return _publicar(registro)

    async def salir_parcial(args: BaseModel) -> ToolResult:
        return _parcial(registro, args)  # type: ignore[arg-type]

    async def mover_stop(args: BaseModel) -> ToolResult:
        return _mover_stop(registro, args)  # type: ignore[arg-type]

    async def dejar_orden(args: BaseModel) -> ToolResult:
        return _dejar_orden(registro, args, max_chars)  # type: ignore[arg-type]

    async def cancelar_orden(args: BaseModel) -> ToolResult:
        return _cancelar_orden(registro, args)  # type: ignore[arg-type]

    async def predecir(args: BaseModel) -> ToolResult:
        return _predecir(registro, args, max_chars)  # type: ignore[arg-type]

    return [
        Tool(
            name="mirar_mercado",
            description=(
                "El mercado. Sin `intervalo` te da los TRES gráficos del mismo instante "
                "(15m, 1h, 4h): precio, dónde está en su rango, régimen medido, ATR, "
                "ADX, RSI, MACD, de qué lado de la EMA, la vela en curso, pools de "
                "liquidez y FVGs. Empezá SIEMPRE por ahí. Con `intervalo` te da uno solo "
                "con más detalle. Usala ANTES de decidir cualquier entrada."
            ),
            args_model=MirarArgs,
            run=mirar,
        ),
        Tool(
            name="abrir_operacion",
            description=(
                "Registra una entrada en papel (sin dinero real). Exige la razón: qué "
                "viste que justifica entrar ACÁ y no cinco velas después. Esa razón "
                "queda sellada y se revisa cuando la operación cierre."
            ),
            args_model=AbrirArgs,
            run=abrir,
        ),
        Tool(
            name="cerrar_operacion",
            description=(
                "Cierra una operación abierta al precio actual y calcula su R múltiplo. "
                "El análisis de qué pasó va en un campo aparte de la razón de entrada."
            ),
            args_model=CerrarArgs,
            run=cerrar,
        ),
        Tool(
            name="estado_paper",
            description=(
                "Qué operaciones quedaron abiertas y cómo va cada eje. Usala al empezar "
                "una sesión: entre una y otra el proceso muere y esto es lo único que "
                "recuerda qué había a medias."
            ),
            args_model=EstadoArgs,
            run=estado,
        ),
        Tool(
            name="salir_parcial",
            description=(
                "Suelta una PARTE de una operación abierta y deja el resto corriendo. "
                "Para tomar ganancia en un primer objetivo sin cerrar del todo. El R de "
                "la operación entera pondera cada tramo por lo que soltó."
            ),
            args_model=ParcialArgs,
            run=salir_parcial,
        ),
        Tool(
            name="mover_stop",
            description=(
                "Mueve el stop de una operación abierta, por ejemplo a la entrada después "
                "de tomar un parcial. El R se sigue midiendo contra el stop original."
            ),
            args_model=MoverStopArgs,
            run=mover_stop,
        ),
        Tool(
            name="dejar_orden",
            description=(
                "Deja una orden límite que entra sola si el precio la toca mientras no "
                "estás operando. Entre sesión y sesión pasan horas: esto es lo que "
                "permite esperar a que el precio VUELVA al nivel que te interesa en vez "
                "de entrar donde esté ahora. La razón se sella al dejarla."
            ),
            args_model=OrdenArgs,
            run=dejar_orden,
        ),
        Tool(
            name="cancelar_orden",
            description="Retira una orden límite que ya no tiene sentido.",
            args_model=CancelarOrdenArgs,
            run=cancelar_orden,
        ),
        Tool(
            name="predecir",
            description=(
                "Apostá una PROBABILIDAD a que el precio toque un nivel antes de que "
                "venza. No cuesta nada —no hay entrada ni stop— y se puede hacer aunque "
                "no operes: es la forma de dejar constancia de qué esperás del mercado. "
                "Se puntúa con Brier, así que la confianza exagerada se castiga."
            ),
            args_model=PredecirArgs,
            run=predecir,
        ),
        Tool(
            name="publicar_historial",
            description=(
                "Publica el historial en el panel web para poder revisarlo desde "
                "fuera. Usala al TERMINAR una sesión: esta máquina se apaga y el "
                "registro deja de ser accesible hasta la próxima."
            ),
            args_model=EstadoArgs,
            run=publicar_historial,
        ),
    ]
