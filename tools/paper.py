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
    intervalo: str = Field(default="15m", description="Temporalidad: 15m, 1h, 4h")


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
        f"volumen relativo a las últimas 50: {ctx.volumen_relativo}x",
        f"día {ctx.dia_semana} (0=lunes), hora {ctx.hora_utc} UTC",
        "",
        f"ATR {ind.get('atr')}",
        f"ADX {adx.get('adx')} (+DI {adx.get('plusDI')} / -DI {adx.get('minusDI')})",
        f"RSI {ind.get('rsi')}",
        f"MACD {macd.get('macd')} sobre señal {macd.get('signal')}",
        f"EMA20 {ind.get('ema')}",
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
            "calcula desde la temporalidad: ~6h en 15m, 24h en 1h, 96h en 4h. Ponelo "
            "a mano solo si tu tesis pide otro plazo, y decí por qué en el "
            "razonamiento."
        ),
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
            content=f"no se registró la predicción: {exc}",
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


def _estado(registro: Registro) -> ToolResult:
    """Lo que quedó abierto y cómo va cada eje. Es lo que el agente lee al volver.

    Entre una sesión y la siguiente el proceso muere, así que esto es lo único
    que sabe qué había a medias.
    """
    abiertas = registro.abiertas()
    ejes = registro.por_eje()

    partes = []
    if abiertas:
        partes.append("Operaciones abiertas:")
        for o in abiertas:
            partes.append(
                f"  #{o['id']} {o['direccion']} {o['simbolo']} a {o['precio_entrada']} "
                f"(stop {o['stop_loss']}) · eje '{o['eje']}' · «{o['razon'][:90]}»"
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
            partes.append(
                f"  #{p['id']} {p['probabilidad']:.0%} de tocar {p['nivel']} "
                f"hacia {p['hacia']} · vence {p['vence_en'][:16]}"
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
        partes.append(
            "Esto no es para elegir qué predecir: es para calibrar el número que decís."
        )
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


def build_paper_tools(ruta_db: str, max_chars: int) -> list[Tool]:
    """Las herramientas de paper trading, sobre un registro concreto."""
    registro = Registro(ruta_db)

    async def mirar(args: BaseModel) -> ToolResult:
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
                "El estado actual de un par: precio, rango de las últimas 20 velas, "
                "volumen relativo, ATR, ADX, RSI, MACD y EMA. Usala ANTES de decidir "
                "cualquier entrada."
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
