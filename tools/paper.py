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
SIEMPRE = ["atr", "adx", "rsi", "macd", "ema"]


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
            "stop, objetivo o manual. Los cierres parciales todavía no se pueden "
            "registrar: cerrá la posición entera o dejala abierta."
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

    rotos = registro.verificar_sellos()
    if rotos:
        partes += [
            "",
            f"⚠ SELLOS ROTOS en {rotos}: esas razones se editaron después de "
            "registrarse y sus operaciones no sirven.",
        ]

    return ToolResult(
        content="\n".join(partes),
        summary={"abiertas": len(abiertas), "ejes": len(ejes)},
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
