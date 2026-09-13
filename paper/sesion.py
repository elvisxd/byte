"""Una sesión de trading en papel, acotada en tiempo.

═══ POR QUÉ EXISTE ═══

El Codespace cobra por hora encendida, no por trabajo hecho: 8 cores son 8
core-hours por hora de reloj, y GitHub Pro da 180 al mes. Un Codespace olvidado
encendido una tarde se come el presupuesto de la semana sin haber operado nada
—ya pasó: 1h13m encendido, 16 core-hours, cero operaciones—.

Así que una sesión no es "arrancar el agente": es **un presupuesto**. Corre N
minutos, publica lo que hizo y termina. El apagado del Codespace lo decide quien
lanza (`--apagar`), porque apagar la máquina desde dentro es una acción que
conviene pedir explícitamente.

═══ QUÉ NO HACE, Y POR QUÉ ═══

⚠ NO DECIDE NADA. No elige entradas, no filtra señales, no vetorea al modelo.
Arma el turno, le da las herramientas y cuenta el tiempo. Si esto empezara a
decidir —"solo entrar si el ADX pasa de X"— habría dos estrategias, la del
modelo y la del script, y el registro no diría cuál produjo cada resultado.

⚠ NO PROMEDIA NI COMPARA EJES. Eso lo hace `por_eje()` y lo enseña el panel, en
orden alfabético. Ver `paper/CRITERIO_ABORTO.md`.

═══ EL TIEMPO ═══

El presupuesto se comprueba ENTRE turnos, no durante uno. Un turno del modelo
grande tarda minutos —qwen3.6:27b va a ~4 min sin GPU— y cortarlo a la mitad
dejaría una herramienta a medio ejecutar: una operación abierta sin registrar,
o registrada sin que el modelo sepa. Se termina el turno en curso y se para
antes del siguiente. Por eso el tope es un mínimo garantizado y no un máximo
exacto, y por eso `--minutos` conviene que sea holgado.
"""

import argparse
import asyncio
import os
import time
from typing import Any

from agent.graph import build_graph
from agent.llm import build_llm
from api.config import Settings
from paper.publicar import publicar
from paper.registro import Registro
from tools.base import ToolRegistry
from tools.paper import build_paper_tools

# ⚠ SOLO BTCUSDT, Y ES UNA DECISIÓN. El símbolo era un argumento libre con un
# default, así que el modelo pedía el par que se le ocurriera: dos sesiones
# podían operar mercados distintos y los ejes quedarían medidos sobre muestras
# que no se pueden comparar entre sí. Con un solo par, la diferencia entre
# operaciones es la hipótesis y no el activo — que es lo que el experimento
# quiere aislar. Añadir pares es una decisión posterior y consciente.
SIMBOLO = "BTCUSDT"

# Cada vuelta del bucle es una pregunta al modelo. Con el modelo grande sin GPU
# cada una tarda varios minutos, así que un presupuesto de 30 min son ~6 vueltas.
INSTRUCCION = f"""Estás operando en papel sobre {SIMBOLO}, sin dinero real.

Esto es lo que tenés que hacer AHORA, en este turno:

1. Mirá el estado del registro con `estado_paper`: qué quedó abierto de antes y
   cómo va cada eje.
2. Si hay operaciones abiertas, mirá el mercado y decidí sobre CADA una: dejarla
   correr, tomar un parcial, mover el stop o cerrarla. Usá las herramientas.
3. Si no hay ninguna abierta —o si además ves una entrada clara— mirá el mercado
   y decidí si entrar. Si entrás, la razón tiene que decir qué viste que
   justifica entrar ACÁ y no cinco velas después.
4. Si no hay nada que hacer, decilo y no operes. No entrar es una decisión
   válida: forzar una entrada para "aprovechar la sesión" contamina el eje.

Los ejes disponibles son range-sweep, zone-reclaim, cvd-divergence, dip-trap y
anti-smc. Elegí el que corresponda a lo que estás viendo; no inventes otros.

No compares ejes entre sí para elegir "el que va mejor": todos corren en
paralelo a propósito y elegir mirando la tabla es sobreajuste."""


async def una_sesion(
    *,
    minutos: float,
    ruta_db: str,
    ruta_scripts: str,
    publicar_al_final: bool = True,
) -> dict[str, Any]:
    """Corre una sesión y devuelve qué pasó."""
    ajustes = Settings()  # type: ignore[call-arg]
    os.environ.setdefault("BYTE_PAPER_SCRIPTS", ruta_scripts)

    registro = Registro(ruta_db)
    # Solo las del experimento: sin CV, sin GitHub, sin navegador. Cada
    # herramienta de más son tokens de definiciones compitiendo con el contexto
    # del gráfico, y ya está medido que el modelo elige peor cuantas más hay.
    herramientas = ToolRegistry(build_paper_tools(ruta_db, ajustes.max_tool_result_chars))

    # Sin checkpointer: cada turno se arma con el estado que el agente LEE del
    # registro, no con el historial del chat. Es lo que hace que una sesión
    # nueva —proceso nuevo, máquina nueva— continúe de donde quedó la anterior.
    grafo = build_graph(
        build_llm(ajustes),
        herramientas,
        max_iterations=ajustes.max_iterations,
        max_tool_result_chars=ajustes.max_tool_result_chars,
        num_ctx=ajustes.ollama_num_ctx,
    )

    limite = time.monotonic() + minutos * 60
    vueltas, errores, seguidos = 0, [], 0
    abiertas_antes = len(registro.abiertas())

    while time.monotonic() < limite:
        vueltas += 1
        restante = (limite - time.monotonic()) / 60
        print(f"[sesión] vuelta {vueltas} · quedan {restante:.0f} min", flush=True)
        try:
            # El estado va COMPLETO: `iterations` y los acumuladores no tienen
            # default en el grafo, y sin ellos el primer nodo revienta con un
            # KeyError que parece un fallo del modelo.
            await grafo.ainvoke(
                {
                    "messages": [{"role": "user", "content": INSTRUCCION}],
                    "iterations": 0,
                    "sources": [],
                    "tools_used": [],
                },
                {"configurable": {"thread_id": f"papel-{vueltas}"}},
            )
        except Exception as exc:  # noqa: BLE001 — una vuelta mala no mata la sesión
            # Un fallo de red o un timeout del modelo no debe perder lo que ya
            # se registró: se anota y se sigue con la vuelta siguiente.
            errores.append(str(exc)[:200])
            print(f"[sesión] vuelta {vueltas} falló: {str(exc)[:120]}", flush=True)
            # ⚠ SE ESPERA ANTES DE REINTENTAR. Sin esto, un error inmediato
            # —Ollama caído, una clave mal— hace girar el bucle a toda velocidad:
            # medido, 13 vueltas fallidas en 30 segundos, que en un codespace es
            # quemar cuota para no hacer nada. Y si fallan varias seguidas, algo
            # está roto de verdad y seguir intentando no lo va a arreglar.
            seguidos += 1
            if seguidos >= 3:
                print("[sesión] tres fallos seguidos: se corta.", flush=True)
                break
            await asyncio.sleep(min(10 * seguidos, 30))
        else:
            seguidos = 0

    resumen = {
        "vueltas": vueltas,
        "abiertas_antes": abiertas_antes,
        "abiertas_ahora": len(registro.abiertas()),
        "por_eje": registro.por_eje(),
        "sellos_rotos": registro.verificar_sellos(),
        "errores": errores,
    }

    # ⚠ SE PUBLICA AUNQUE LA SESIÓN HAYA IDO MAL. Es la única copia que sobrevive
    # al Codespace, y una sesión con errores es justamente la que hay que poder
    # mirar después. Publicar es lo último que pasa, pase lo que pase.
    if publicar_al_final:
        try:
            foto = publicar(registro)
            resumen["publicado"] = True
            resumen["operaciones_publicadas"] = len(foto["cerradas"]) + len(foto["abiertas"])
        except Exception as exc:  # noqa: BLE001
            resumen["publicado"] = False
            resumen["error_publicar"] = str(exc)[:200]

    registro.cerrar_conexion()
    return resumen


def main() -> None:
    parser = argparse.ArgumentParser(description="Una sesión de trading en papel, acotada.")
    parser.add_argument(
        "--minutos",
        type=float,
        default=30.0,
        help="Presupuesto de tiempo. Se comprueba ENTRE turnos, así que la sesión "
        "puede pasarse por lo que dure el último (minutos, con el modelo grande).",
    )
    parser.add_argument("--db", default=os.environ.get("BYTE_PAPER_DB", "paper/operaciones.db"))
    parser.add_argument("--scripts", default=os.environ.get("BYTE_PAPER_SCRIPTS", ""))
    parser.add_argument(
        "--sin-publicar",
        action="store_true",
        help="No empuja al panel. Para probar sin tocar el historial de verdad.",
    )
    args = parser.parse_args()

    resumen = asyncio.run(
        una_sesion(
            minutos=args.minutos,
            ruta_db=args.db,
            ruta_scripts=args.scripts,
            publicar_al_final=not args.sin_publicar,
        )
    )

    print("\n─── la sesión terminó ───")
    print(f"vueltas: {resumen['vueltas']}")
    print(f"abiertas: {resumen['abiertas_antes']} → {resumen['abiertas_ahora']}")
    for e in resumen["por_eje"]:
        print(f"  {e['eje']}: {e['cerradas']} cerradas, R total {e['r_total']}")
    if resumen["sellos_rotos"]:
        print(f"⚠ SELLOS ROTOS: {resumen['sellos_rotos']}")
    if resumen["errores"]:
        print(f"vueltas con error: {len(resumen['errores'])}")
    print("publicado en el panel" if resumen.get("publicado") else "NO se publicó")


if __name__ == "__main__":
    main()
