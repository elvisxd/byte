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
from agent.llm import build_llm, es_de_groq, es_remoto
from agent.relevo import Relevo
from api.config import Settings
from paper.mercado import MercadoNoDisponible, indicadores
from paper.mercado import velas as velas_del_mercado
from paper.prompt import INSTRUCCION, SIMBOLO  # noqa: F401 — SIMBOLO se reexporta
from paper.publicar import publicar
from paper.registro import Registro
from paper.trace import TraceDeSesion
from tools.base import ToolRegistry
from tools.paper import build_paper_tools

# Cuánto se espera entre vueltas. Ver paper/CRITERIO_CADENCIA.md, que se
# escribió antes que este código y en su propio commit.
FRACCION_ATR = 0.5
MINUTOS_POR_MARCO = {"15m": 15, "1h": 60, "4h": 240}
# El marco de la espera. Era 15m; el 2026-09-14 pasó a 1h —ver la sección de
# ese día en CRITERIO_CADENCIA.md—: una vuelta dura 25-40 min y la vela de 15m
# cerraba siempre antes de terminar de pensar, o sea que la espera no esperaba
# nada, y el gráfico que decide —la estructura de 4h, el régimen de 1h— no
# cambia en un cuarto de hora. Órdenes y predicciones se siguen resolviendo
# contra velas de 15m: ahí la granularidad fina sí importa.
MARCO_DE_LA_ESPERA = "1h"


async def _esperar_algo_nuevo(limite: float, marco: str = "15m") -> None:
    """Espera a que haya gráfico nuevo que mirar, o a que se acabe la sesión.

    ⚠ SIN ESTO EL AGENTE MIRA EL MISMO INSTANTE UNA Y OTRA VEZ. Medido el
    2026-09-14 con qwen3:14b: 31 vueltas en 40 minutos, 23 agotando las
    iteraciones sin registrar nada, y tres órdenes idénticas seguidas. Entre la
    primera vuelta y la quinta, BTC se movió 20,7 dólares —un 0,03%— con un ATR
    de 170. El modelo no fallaba: repetía porque no había nada nuevo.

    Se sale por lo que ocurra primero: media vela de movimiento, el cierre de
    una vela, o el tope de dos velas —un mercado quieto también es un dato, y
    una sesión que no da ninguna vuelta no registra la abstención—.

    Nunca se espera más allá del presupuesto: si la sesión termina en dos
    minutos, no tiene sentido dormir quince.
    """
    minutos_vela = MINUTOS_POR_MARCO.get(marco, 15)
    tope = time.monotonic() + minutos_vela * 2 * 60

    try:
        datos = velas_del_mercado(SIMBOLO, marco, 200)
    except MercadoNoDisponible:
        # Sin mercado no se puede comparar nada. Se espera una vela y se sigue:
        # el modelo verá lo que haya cuando vuelva.
        await asyncio.sleep(min(minutos_vela * 60, max(0.0, limite - time.monotonic())))
        return

    referencia = datos["velas"][-1]["close"]
    ultima_vela = datos["velas"][-1]["time"]
    indicadores_ref = indicadores(datos["velas"], ["atr"])
    atr = indicadores_ref.get("atr")
    umbral = atr * FRACCION_ATR if isinstance(atr, int | float) and atr > 0 else None

    while True:
        # El presupuesto manda sobre todo lo demás.
        if time.monotonic() >= limite or time.monotonic() >= tope:
            return
        # Un minuto entre sondeos: BTC no se mueve medio ATR en menos, y cada
        # consulta es una llamada al exchange.
        await asyncio.sleep(min(60.0, max(1.0, limite - time.monotonic())))
        try:
            ahora_datos = velas_del_mercado(SIMBOLO, marco, 2)
        except MercadoNoDisponible:
            continue
        vela = ahora_datos["velas"][-1]
        # Cerró una vela: hay información nueva por construcción.
        if vela["time"] != ultima_vela:
            return
        if umbral is not None and abs(vela["close"] - referencia) >= umbral:
            return


def armar(
    ajustes: Settings, ruta_db: str, modelos: list[str] | None = None
) -> tuple[Registro, Any, Any]:
    """El registro, el grafo y la etiqueta del modelo. Lo comparten la sesión y el vigía.

    `modelos` es la lista del brazo remoto (paper/CRITERIO_COMPARACION.md): varios
    `gemini-…` que se turnan ante un 429. Sin ella, el modelo local de siempre.
    La etiqueta que devuelve es un str para el local y un CALLABLE para el
    remoto —el modelo que contesta cambia con el relevo—, y tanto el registro
    como la traza lo resuelven al escribir.
    """
    registro = Registro(ruta_db)
    nombres = [n.strip() for n in (modelos or []) if n.strip()]
    if nombres:
        return _armar_remoto(ajustes, ruta_db, nombres, registro)
    # ⚠ EL MODELO QUE SE REGISTRA DICE SI PENSÓ. `qwen3:14b` sin razonamiento y
    # con él son dos agentes distintos —medido el 2026-09-14: el primero
    # escribe la misma frase 31 veces en una sesión, el segundo tarda diez
    # minutos por iteración—, y la columna `modelo` es lo único que permite
    # separarlos cuando alguien mire el registro dentro de una semana. Sin la
    # marca, las dos configuraciones caerían en el mismo eje sin que nada lo
    # dijera, que es exactamente lo que `EJES.md` prohíbe mezclar.
    etiqueta_modelo = ajustes.ollama_model + ("+razona" if ajustes.paper_reasoning else "")

    # Solo las del experimento: sin CV, sin GitHub, sin navegador. Cada
    # herramienta de más son tokens de definiciones compitiendo con el contexto
    # del gráfico, y ya está medido que el modelo elige peor cuantas más hay.
    herramientas = ToolRegistry(
        build_paper_tools(ruta_db, ajustes.paper_max_tool_result_chars, etiqueta_modelo)
    )

    # Sin checkpointer: cada turno se arma con el estado que el agente LEE del
    # registro, no con el historial del chat. Es lo que hace que una sesión
    # nueva —proceso nuevo, máquina nueva— continúe de donde quedó la anterior.
    #
    # ⚠ EL RAZONAMIENTO SE PIDE ACÁ Y NO EN `build_llm`. La API comparte esa
    # función y su latencia no tiene que pagar por esto: medido el 2026-09-14,
    # la misma pregunta pasa de 88 s a 279 s con el pensamiento encendido.
    #
    # Se enciende porque `qwen3:14b` no reacciona a los rechazos: 11 vueltas,
    # 11 topes de iteraciones y cero escrituras en la sesión de esa mañana,
    # reintentando los mismos tres niveles ya ocupados. Preguntado directamente,
    # compara las predicciones ENTRE SÍ en vez de medir la distancia de la
    # nueva, y concluye que puede registrar otra: reintenta porque cree que
    # puede. Dos rondas de arreglar eso con más texto en el prompt no lo
    # movieron —ver el commit de `BAJÁ DE MARCO`—, así que el problema no era
    # cómo estaba escrita la regla.
    #
    # `paper_num_predict` va con él por lo que dice `build_llm`: son un par.
    grafo = build_graph(
        build_llm(
            ajustes,
            reasoning=ajustes.paper_reasoning,
            num_predict=ajustes.paper_num_predict,
            # 12K, no 16K: es lo que cabe entero en la GPU de un Mac de 16 GB.
            # Ver `paper_num_ctx` en api/config.py, con la medición.
            num_ctx=ajustes.paper_num_ctx,
        ),
        herramientas,
        max_iterations=ajustes.max_iterations,
        max_tool_result_chars=ajustes.paper_max_tool_result_chars,
        num_ctx=ajustes.paper_num_ctx,
    )
    return registro, grafo, etiqueta_modelo


def _armar_remoto(
    ajustes: Settings, ruta_db: str, nombres: list[str], registro: Registro
) -> tuple[Registro, Any, Any]:
    """Un brazo remoto: un relevo de modelos por API, mismo prompt, mismas herramientas.

    Todo lo que NO cambia respecto al local es a propósito: el tope de
    iteraciones, el recorte de los resultados, el presupuesto de historial.
    Ver «Qué se compara, y qué se mantiene igual» en el criterio.
    """
    if not all(es_remoto(n) for n in nombres):
        raise ValueError(f"el relevo mezcla modelos locales y remotos: {nombres}")
    # La espera entre llamadas es la del proveedor más lento de la lista: el
    # tope por minuto de Groq es de tokens y obliga a 45 s (ver api/config.py).
    espera_s = ajustes.gemini_espera_s
    if any(es_de_groq(n) for n in nombres):
        espera_s = max(espera_s, ajustes.groq_espera_s)
    relevo = Relevo(
        # `reasoning=True`: pedir VER el pensamiento, que es lo que se audita en
        # la traza. Los modelos que piensan lo hacen igual con o sin esto.
        [(n, build_llm(ajustes, n, reasoning=True)) for n in nombres],
        espera_s=espera_s,
    )

    def etiqueta() -> str:
        return relevo.actual

    herramientas = ToolRegistry(
        build_paper_tools(ruta_db, ajustes.paper_max_tool_result_chars, etiqueta)
    )
    grafo = build_graph(
        relevo,
        herramientas,
        max_iterations=ajustes.max_iterations,
        max_tool_result_chars=ajustes.paper_max_tool_result_chars,
        num_ctx=ajustes.paper_num_ctx,
    )
    return registro, grafo, etiqueta


def poner_al_dia(registro: Registro, prefijo: str = "[sesión]") -> dict[str, list[dict[str, Any]]]:
    """Lo que pasó mientras nadie miraba. Con código, sin modelo.

    ⚠ VA ANTES DE QUE EL MODELO MIRE. Las órdenes límite existen justamente
    para cubrir las horas sin nadie mirando: una orden que se disparó anoche
    es una operación abierta, y el agente tiene que saberlo antes de
    plantearse entrar otra vez. Un stop atravesado mientras se esperaba
    gráfico nuevo no espera a que el modelo lo cierre horas después a otro
    precio. Y una predicción que tocó su nivel se resuelve sola.

    Se hace con código y no pidiéndoselo al modelo por la misma razón que el R
    múltiplo: es aritmética sobre las velas —¿el precio tocó el nivel?— y no
    una decisión. Pedírselo al modelo sería dejarle puntuarse a sí mismo.
    """
    cambios: dict[str, list[dict[str, Any]]] = {"ordenes": [], "predicciones": [], "cerradas": []}
    try:
        historico = velas_del_mercado(SIMBOLO, "15m", 200)
    except MercadoNoDisponible as exc:
        # Sin datos no se puede saber qué pasó. Se avisa y se sigue: el modelo
        # verá lo que haya vivo y decidirá.
        print(f"{prefijo} no se pudo poner al día: {exc}", flush=True)
        return cambios
    velas = historico["velas"]
    cambios["ordenes"] = registro.evaluar_ordenes(velas)
    for d in cambios["ordenes"]:
        print(f"{prefijo} orden #{d['id']}: {d['resultado']}", flush=True)
    cambios["predicciones"] = registro.resolver_predicciones(velas)
    for pr in cambios["predicciones"]:
        print(
            f"{prefijo} predicción #{pr['id']}: dijo {pr['probabilidad']:.0%}, "
            f"{'ocurrió' if pr['ocurrio'] else 'no ocurrió'} (Brier {pr['brier']})",
            flush=True,
        )
    # Después de las órdenes a propósito: una que se disparó a una hora pasada
    # puede haber tocado su stop después, en el mismo período sin vigilancia.
    cambios["cerradas"] = registro.evaluar_abiertas(velas)
    for c in cambios["cerradas"]:
        print(
            f"{prefijo} operación #{c['id']} cerrada por {c['motivo']} "
            f"a {c['precio_salida']} · R {c['r']:+.2f}",
            flush=True,
        )
    return cambios


async def una_vuelta(grafo: Any, trace: TraceDeSesion, numero: int) -> str | None:
    """Una pregunta al modelo. Devuelve el error si falló; None si fue bien."""
    trace.vuelta = numero
    # Se publica ANTES de la vuelta y no solo después: si el modelo tarda
    # cuatro minutos, quien mira tiene que ver que empezó, no una página
    # quieta que no distingue "pensando" de "colgado".
    trace.publicar(viva=True)
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
            {"configurable": {"thread_id": f"papel-{numero}", "emitter": trace}},
        )
    except Exception as exc:  # noqa: BLE001 — una vuelta mala no mata la sesión
        # Un fallo de red o un timeout del modelo no debe perder lo que ya se
        # registró: se anota y quien llama decide si sigue.
        return str(exc)[:200]
    return None


async def una_sesion(
    *,
    minutos: float,
    ruta_db: str,
    ruta_scripts: str,
    publicar_al_final: bool = True,
    modelos: list[str] | None = None,
    archivo_traza: str = "",
) -> dict[str, Any]:
    """Corre una sesión y devuelve qué pasó."""
    ajustes = Settings()  # type: ignore[call-arg]
    os.environ.setdefault("BYTE_PAPER_SCRIPTS", ruta_scripts)

    registro, grafo, etiqueta_modelo = armar(ajustes, ruta_db, modelos)

    # Lo primero: qué pasó mientras no estábamos. Ver `poner_al_dia`.
    disparadas = poner_al_dia(registro)["ordenes"]

    limite = time.monotonic() + minutos * 60
    vueltas, errores, seguidos = 0, [], 0
    abiertas_antes = len(registro.abiertas())

    # El razonamiento, para poder mirarlo desde fuera mientras corre. Va aparte
    # del historial porque son cosas distintas: el historial dice QUÉ decidió y
    # se conserva; el trace dice CÓMO y caduca. Ver paper/trace.py.
    trace = TraceDeSesion(
        sesion_id=f"{int(time.time())}",
        modelo=etiqueta_modelo,
        simbolo=SIMBOLO,
        archivo=archivo_traza,
    )

    while time.monotonic() < limite:
        vueltas += 1
        restante = (limite - time.monotonic()) / 60
        print(f"[sesión] vuelta {vueltas} · quedan {restante:.0f} min", flush=True)
        # En cada vuelta y no solo al arrancar: la espera entre vueltas puede
        # ser de una hora, y en ese rato una orden se dispara o un stop se toca.
        poner_al_dia(registro)
        error = await una_vuelta(grafo, trace, vueltas)
        if error is not None:
            errores.append(error)
            print(f"[sesión] vuelta {vueltas} falló: {error[:120]}", flush=True)
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
            # ⚠ SOLO TRAS UNA VUELTA BUENA. Un fallo ya tiene su propio backoff
            # arriba, y esperar movimiento después de que Ollama se caiga sería
            # sumar dos esperas por el mismo problema.
            await _esperar_algo_nuevo(limite, MARCO_DE_LA_ESPERA)

    # `viva=False` marca el trace como terminado: la página deja de refrescar y
    # dice que la sesión acabó, en vez de esperar pasos que no van a llegar.
    trace.publicar(viva=False)

    resumen = {
        "ordenes_resueltas": disparadas,
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
    parser.add_argument(
        "--modelo",
        default="",
        help="Lista de modelos remotos separados por comas (gemini-…); se turnan ante un 429. "
        "Sin esto, el modelo local de OLLAMA_MODEL.",
    )
    parser.add_argument(
        "--traza", default="", help="Escribe la traza en este archivo, no al panel."
    )
    args = parser.parse_args()

    resumen = asyncio.run(
        una_sesion(
            minutos=args.minutos,
            ruta_db=args.db,
            ruta_scripts=args.scripts,
            publicar_al_final=not args.sin_publicar,
            modelos=args.modelo.split(",") if args.modelo else None,
            archivo_traza=args.traza,
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
