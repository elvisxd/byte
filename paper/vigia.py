"""El vigía: el modelo no corre 24/7, despierta por eventos.

═══ POR QUÉ EXISTE ═══

Ver la sección «modo vigía» del 2026-09-14 en CRITERIO_CADENCIA.md, que se
escribió antes que esto y en su propio commit. En corto: con diez predicciones
vivas y una orden puesta, el modelo ya dijo lo que tenía que decir sobre esa
estructura; el 4h —que la decide— cambia cada cuatro horas, no cada media; y
las órdenes límite hacen la espera por él. Releer el mismo 4h antes de que
cambie es la redundancia que ese archivo describió para el 15m y el 1h, un
marco más arriba.

═══ QUÉ HACE ═══

Cada `CADA_S` segundos, SIN modelo: pone al día lo mecánico —órdenes
disparadas, stops tocados, predicciones resueltas— y mira si ocurrió algo que
justifique despertar al modelo para UNA vuelta:

  1. cerró una vela de 4h (la estructura cambió);
  2. el precio está a menos de `MARGEN_ATR_EVENTO` ATR de 15m de un nivel vivo
     —una orden, una predicción, un pool con fuerza—: el timing, el único
     papel del 15m;
  3. hay una posición abierta y el precio está a menos de ese margen de su stop
     o de su objetivo.

Con un tope diario de vueltas y una ventana activa en hora local: fuera de
ella sigue poniendo al día, pero no despierta al modelo. El reposo es un
requisito —la máquina se usa para otras cosas y hay que saber cuándo—, no un
efecto.

⚠ NO DECIDE NADA. Igual que la sesión: no elige entradas ni filtra señales.
Decide cuándo hay algo nuevo que mirar, y eso es cadencia, no estrategia.

═══ CÓMO SE PARA ═══

Con SIGTERM o SIGINT: termina el sondeo en curso, marca la traza como acabada
y publica el historial. El script que lo lanza manda TERM al pararse.
"""

import argparse
import asyncio
import os
import signal
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from datetime import time as hora_del_dia
from typing import Any

from api.config import Settings
from paper.mercado import MercadoNoDisponible, indicadores
from paper.mercado import velas as velas_del_mercado
from paper.publicar import publicar
from paper.registro import Registro
from paper.sesion import SIMBOLO, armar, poner_al_dia, una_vuelta
from paper.trace import TraceDeSesion

# Ventana activa, hora local de la máquina. Ver CRITERIO_CADENCIA.md: cubre los
# cierres de 4h de las 08, 12, 16 y 20 local y la sesión americana. Fuera de
# ella, reposo garantizado.
VENTANA = (hora_del_dia(8, 0), hora_del_dia(20, 30))
# Para que un día nervioso no lo vuelva 24/7 por la puerta de atrás.
TOPE_DIARIO = 8
# A cuántos ATR de 15m de un nivel vivo se considera que el precio «se acerca».
MARGEN_ATR_EVENTO = 1.0
# Cada cuánto se sondea. Quince minutos: la vela de la resolución.
CADA_S = 900


def en_ventana(ahora: datetime, ventana: tuple[hora_del_dia, hora_del_dia] = VENTANA) -> bool:
    return ventana[0] <= ahora.time() <= ventana[1]


def eventos(
    registro: Registro,
    *,
    precio: float,
    atr_15m: float,
    pools: list[dict[str, Any]],
    cierre_4h: int | None,
    cierre_4h_visto: int | None,
    plazos_avisados: set[int] | None = None,
) -> list[str]:
    """Qué justifica despertar al modelo ahora. Vacío si nada.

    Solo hechos comparables: un tiempo de vela que cambió, una distancia en
    ATR. Nada de «parece que va a romper».
    """
    motivos: list[str] = []
    if cierre_4h is not None and cierre_4h_visto is not None and cierre_4h != cierre_4h_visto:
        cuando = datetime.fromtimestamp(cierre_4h).astimezone().strftime("%H:%M")
        motivos.append(f"cerró la vela de 4h de las {cuando}")
    if atr_15m <= 0:
        return motivos
    margen = atr_15m * MARGEN_ATR_EVENTO
    for o in registro.ordenes_vivas():
        if abs(precio - o["precio_limite"]) <= margen:
            motivos.append(
                f"el precio está a {abs(precio - o['precio_limite']):.0f} de la orden "
                f"#{o['id']} ({o['precio_limite']})"
            )
    for p in registro.predicciones_vivas():
        if abs(precio - p["nivel"]) <= margen:
            motivos.append(
                f"el precio está a {abs(precio - p['nivel']):.0f} de la predicción "
                f"#{p['id']} ({p['nivel']})"
            )
    for pool in pools:
        if pool.get("fuerza", 0) >= 2 and abs(precio - pool["precio"]) <= margen:
            motivos.append(
                f"el precio está a {abs(precio - pool['precio']):.0f} del pool "
                f"{pool['precio']} (f{pool['fuerza']})"
            )
    for op in registro.abiertas():
        # El cuarto evento (CRITERIO_GESTION.md): la tesis agotó el plazo de su
        # marco sin tocar stop ni objetivo. Se avisa UNA vez por operación: si
        # el modelo decide seguir, no se le vuelve a despertar por esto cada
        # cuarto de hora, que se comería el tope diario.
        if registro.agoto_plazo(op) and (
            plazos_avisados is None or op["id"] not in plazos_avisados
        ):
            plazo = registro.plazo_de(op)
            motivos.append(f"la operación #{op['id']} agotó su plazo de {plazo:.0f} h: decidí")
        stop = op["stop_actual"] if op["stop_actual"] is not None else op["stop_loss"]
        if abs(precio - stop) <= margen:
            motivos.append(
                f"la operación #{op['id']} está a {abs(precio - stop):.0f} de su stop ({stop})"
            )
        if op["take_profit"] and abs(precio - op["take_profit"]) <= margen:
            motivos.append(
                f"la operación #{op['id']} está a {abs(precio - op['take_profit']):.0f} "
                "de su objetivo"
            )
    return motivos


async def vigilar(
    *,
    ruta_db: str,
    ruta_scripts: str,
    ventana: tuple[hora_del_dia, hora_del_dia] = VENTANA,
    tope_diario: int = TOPE_DIARIO,
    cada_s: float = CADA_S,
    primera_vuelta_al_arrancar: bool = True,
    # Puntos de inyección para probarlo sin modelo, sin red y sin reloj.
    ahora: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
    correr_vuelta: Callable[[int], Awaitable[str | None]] | None = None,
    ticks: int | None = None,
) -> dict[str, Any]:
    """El bucle. Termina por señal o, en pruebas, tras `ticks` sondeos."""
    ajustes = Settings()  # type: ignore[call-arg]
    os.environ.setdefault("BYTE_PAPER_SCRIPTS", ruta_scripts)
    registro, grafo, etiqueta = armar(ajustes, ruta_db)
    trace = TraceDeSesion(sesion_id=f"vigia-{int(time.time())}", modelo=etiqueta, simbolo=SIMBOLO)

    if correr_vuelta is None:

        async def correr_vuelta(n: int) -> str | None:
            return await una_vuelta(grafo, trace, n)

    parando = False

    def _parar(*_: Any) -> None:
        nonlocal parando
        parando = True

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _parar)
    except (NotImplementedError, RuntimeError):
        pass

    vueltas_total = 0
    vueltas_hoy: dict[str, int] = {}
    cierre_4h_visto: int | None = None
    plazos_avisados: set[int] = set()
    pendiente_arranque = primera_vuelta_al_arrancar
    ultimo_latido = 0.0
    n_ticks = 0

    while not parando and (ticks is None or n_ticks < ticks):
        n_ticks += 1
        momento = ahora()
        dia = momento.strftime("%Y-%m-%d")
        cambios = poner_al_dia(registro, prefijo="[vigía]")
        hubo_cambios = any(cambios.values())

        motivos: list[str] = []
        try:
            v15 = velas_del_mercado(SIMBOLO, "15m", 200)
            v4h = velas_del_mercado(SIMBOLO, "4h", 3)
            ind = indicadores(v15["velas"], ["atr", "liquidity"])
            precio = float(v15["velas"][-1]["close"])
            atr = ind.get("atr")
            atr_15m = float(atr) if isinstance(atr, int | float) else 0.0
            # La última vela CERRADA de 4h es la penúltima: la última está en curso.
            cierre_4h = int(v4h["velas"][-2]["time"]) if len(v4h["velas"]) >= 2 else None
            motivos = eventos(
                registro,
                precio=precio,
                atr_15m=atr_15m,
                pools=ind.get("liquidity") or [],
                cierre_4h=cierre_4h,
                cierre_4h_visto=cierre_4h_visto,
                plazos_avisados=plazos_avisados,
            )
            # Se anota SIEMPRE, dentro o fuera de la ventana: si no, el cierre de
            # las 04 dispararía la vuelta de las 08, y la de las 08 tiene que
            # ser por el cierre de las 08.
            cierre_4h_visto = cierre_4h
        except MercadoNoDisponible as exc:
            print(f"[vigía] sin mercado: {exc}", flush=True)
            precio = 0.0

        if pendiente_arranque:
            motivos.insert(0, "arranque: una vuelta de lectura")
            pendiente_arranque = False

        dentro = en_ventana(momento, ventana)
        hoy = vueltas_hoy.get(dia, 0)
        if motivos and dentro and hoy < tope_diario:
            vueltas_total += 1
            vueltas_hoy[dia] = hoy + 1
            print(
                f"[vigía] {momento:%H:%M} · vuelta {vueltas_total} ({hoy + 1}/{tope_diario} hoy) · "
                + "; ".join(motivos),
                flush=True,
            )
            # Los plazos por los que se despertó quedan avisados, se cierre o no.
            plazos_avisados.update(
                op["id"] for op in registro.abiertas() if registro.agoto_plazo(op)
            )
            error = await correr_vuelta(vueltas_total)
            if error:
                print(f"[vigía] la vuelta {vueltas_total} falló: {error[:120]}", flush=True)
            hubo_cambios = True
        elif motivos:
            por = "fuera de la ventana" if not dentro else f"tope diario ({tope_diario}) alcanzado"
            print(
                f"[vigía] {momento:%H:%M} · había motivo ({motivos[0]}) pero {por}: reposo",
                flush=True,
            )
        elif time.monotonic() - ultimo_latido >= 3600:
            estado = "ventana" if dentro else "reposo"
            print(
                f"[vigía] {momento:%H:%M} · precio {precio} · sin eventos · "
                f"{hoy}/{tope_diario} hoy · {estado}",
                flush=True,
            )
            ultimo_latido = time.monotonic()

        if hubo_cambios:
            try:
                publicar(registro)
            except Exception as exc:  # noqa: BLE001 — publicar es best-effort
                print(f"[vigía] no se publicó: {str(exc)[:100]}", flush=True)

        # Se duerme a trozos para que la señal de parada se atienda en segundos.
        restante = cada_s
        while restante > 0 and not parando:
            paso = min(5.0, restante)
            await dormir(paso)
            restante -= paso

    trace.publicar(viva=False)
    try:
        publicar(registro)
    except Exception as exc:  # noqa: BLE001 — publicar es best-effort, y esto ya se está parando
        print(f"[vigía] al parar no se publicó: {str(exc)[:100]}", flush=True)
    registro.cerrar_conexion()
    return {"vueltas": vueltas_total, "por_dia": vueltas_hoy, "ticks": n_ticks}


def main() -> None:
    parser = argparse.ArgumentParser(description="El vigía: despierta al modelo por eventos.")
    parser.add_argument("--db", default=os.environ.get("BYTE_PAPER_DB", "paper/operaciones.db"))
    parser.add_argument("--scripts", default=os.environ.get("BYTE_PAPER_SCRIPTS", ""))
    parser.add_argument("--sin-vuelta-al-arrancar", action="store_true")
    args = parser.parse_args()
    resumen = asyncio.run(
        vigilar(
            ruta_db=args.db,
            ruta_scripts=args.scripts,
            primera_vuelta_al_arrancar=not args.sin_vuelta_al_arrancar,
        )
    )
    print(
        f"\n─── el vigía se paró ─── vueltas: {resumen['vueltas']} · por día: {resumen['por_dia']}"
    )


if __name__ == "__main__":
    main()
