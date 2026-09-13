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
        raise MercadoNoDisponible(str(datos["error"]))
    logger.info("velas", simbolo=simbolo, fuente=datos.get("fuente"), cuantas=len(datos["velas"]))
    return datos


def indicadores(lista_velas: list[dict[str, Any]], pedidos: list[str]) -> dict[str, Any]:
    """Los indicadores sobre esas velas, calculados por el código de siempre."""
    return _node(
        "calcular.mjs",
        entrada=json.dumps({"velas": lista_velas, "pedidos": pedidos}),
    )
