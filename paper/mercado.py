"""El puente a las velas y los indicadores, que viven en el repo de trading.

Los indicadores son 3.200 líneas de TypeScript que estuvieron en producción, y
se invocan con Node en vez de reescribirlos: una reescritura puede diferir del
original en un detalle y entonces nada de lo que se mida acá es comparable con
lo que ya se midió allá.

Un proceso por llamada. Son milisegundos, y mantener un servidor vivo entre
sesiones es exactamente lo que no se puede hacer en un entorno que se apaga.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from api.logging import get_logger

logger = get_logger("paper.mercado")

TIMEOUT_S = 45
# Node 22 ejecuta TypeScript sin compilar con esta bandera. `--no-warnings`
# porque el aviso de "experimental" ensuciaría cada llamada.
NODE_ARGS = ["--experimental-strip-types", "--no-warnings"]


class MercadoNoDisponible(RuntimeError):
    """Falta Node, faltan los scripts, o ningún exchange respondió."""


# ═══ EL ESTADO DE LAS FUENTES DE VELAS ═══
#
# ⚠ LA CASCADA TAPA LAS AVERÍAS, Y ESO ES LO QUE SE VIGILA. `velas.mjs` prueba
# MEXC → Binance → Bybit y devuelve la primera que conteste, así que con MEXC
# caído todo sigue funcionando y NADIE se entera: el vigía registra la fuente
# que sirvió y tira la lista de las que fallaron. Es el mismo modo degradado
# invisible que este proyecto ya se comió dos veces (el traspaso de tramos, el
# contador de suelos), y la lección escrita es que un degradado que no se ve
# desde fuera dura meses.
#
# ⚠ Y NO ES COSMÉTICO PARA EL EXPERIMENTO: Binance da `takerBuyVolume` y los
# demás no, así que con Binance caído el agente pierde el CVD —uno de los cinco
# ejes— sin que el registro diga por qué se quedó sin operar ese eje.
#
# Es un contador en memoria y muere con el proceso, a propósito: lo que se
# quiere saber es si las fuentes están respondiendo AHORA, y el vigía publica
# su foto en cada cambio. Persistirlo obligaría a decidir cuándo caduca.
_FUENTES: dict[str, dict[str, Any]] = {}


def _anotar_fuentes(fuente: str, fallos: list[str]) -> None:
    for entrada in fallos:
        nombre, _, motivo = str(entrada).partition(":")
        est = _FUENTES.setdefault(nombre.strip(), {"sirvio": 0, "fallo": 0, "ultimo_error": None})
        est["fallo"] += 1
        est["ultimo_error"] = motivo.strip()[:80] or "sin detalle"
    if fuente:
        est = _FUENTES.setdefault(fuente, {"sirvio": 0, "fallo": 0, "ultimo_error": None})
        est["sirvio"] += 1


def estado_fuentes() -> dict[str, dict[str, Any]]:
    """Qué fuente de velas sirvió y cuál falló, desde que arrancó el vigía.

    En orden alfabético y NUNCA por fiabilidad: es un parte, no un ranking.
    """
    return {nombre: dict(est) for nombre, est in sorted(_FUENTES.items())}


def _carpeta() -> Path:
    """Dónde viven los scripts. Configurable porque el repo de trading es otro."""
    ruta = os.environ.get("BYTE_PAPER_SCRIPTS", "")
    if not ruta:
        raise MercadoNoDisponible(
            "falta BYTE_PAPER_SCRIPTS: la carpeta scripts/paper del repo de trading"
        )
    carpeta = Path(ruta).expanduser()
    if not (carpeta / "velas.mjs").is_file():
        raise MercadoNoDisponible(f"no encuentro velas.mjs en {carpeta}")
    return carpeta


def _node(script: str, *args: str, entrada: str = "") -> Any:
    binario = shutil.which("node")
    if not binario:
        raise MercadoNoDisponible("node no está instalado")
    carpeta = _carpeta()
    proceso = subprocess.run(  # noqa: S603 - rutas resueltas, argumentos por lista
        [binario, *NODE_ARGS, str(carpeta / script), *args],
        input=entrada,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
        cwd=carpeta,
    )
    if proceso.returncode != 0 and not proceso.stdout.strip():
        raise MercadoNoDisponible((proceso.stderr or "node falló").strip()[:300])
    try:
        return json.loads(proceso.stdout)
    except json.JSONDecodeError as exc:
        raise MercadoNoDisponible(f"node devolvió algo que no es JSON: {exc}") from exc


def velas(simbolo: str, intervalo: str = "15m", cuantas: int = 200) -> dict[str, Any]:
    """Las velas del primer exchange que responda.

    Devuelve también de cuál salieron: no es un detalle: Binance da
    `takerBuyVolume` y los demás no, así que qué indicadores se pueden calcular
    depende de quién contestó.
    """
    datos = _node("velas.mjs", simbolo, intervalo, str(cuantas))
    if "error" in datos:
        # Ninguna fuente respondió: el mensaje trae la lista entera de fallos
        # («mexc: HTTP 400; binance: …»), que es justo lo que hay que anotar.
        _anotar_fuentes("", str(datos["error"]).split("—")[-1].split(";"))
        raise MercadoNoDisponible(str(datos["error"]))
    _anotar_fuentes(str(datos.get("fuente") or ""), list(datos.get("fallos") or []))
    logger.info(
        "velas",
        simbolo=simbolo,
        fuente=datos.get("fuente"),
        cuantas=len(datos["velas"]),
        # Los que fallaron ANTES del que sirvió. Sin esto, una fuente caída es
        # invisible mientras la siguiente de la cascada conteste.
        fallos=datos.get("fallos") or None,
    )
    return datos


def indicadores(lista_velas: list[dict[str, Any]], pedidos: list[str]) -> dict[str, Any]:
    """Los indicadores sobre esas velas, calculados por el código de siempre."""
    return _node(
        "calcular.mjs",
        entrada=json.dumps({"velas": lista_velas, "pedidos": pedidos}),
    )
