"""Publica el registro en papel para que el panel de trading lo lea.

El registro vive en SQLite dentro del Codespace, y el Codespace muere. El panel
corre en Railway y no puede abrir ese archivo. Así que lo que viaja no es la
base: es una **foto** de la base, empujada a la misma Redis que ya usa el panel.

**Por qué una foto y no una API.** El panel tendría que llegar al Codespace, que
la mitad del tiempo no existe. Con la foto, el panel lee lo último que se
publicó y dice cuándo fue. Un historial de hace tres horas sirve; un panel que
no carga porque la máquina que lo alimenta está apagada, no.

**Una sola clave, y esto no es pereza.** El adaptador de Redis de ese servicio
expone `get`, `mget` y `set`, nada más: no hay `scan` ni `keys`, así que una
clave por operación sería imposible de listar. Es la misma razón por la que
`operaciones.ts` guarda un array entero bajo una clave. Ver su cabecera.

**Sin TTL.** Mismo criterio que el registro de operaciones del usuario: esto no
se puede volver a pedir de ninguna parte si se borra. Un TTL vaciaría el
historial en silencio justo cuando el Codespace lleva días apagado, que es
exactamente cuando alguien mira el panel.
"""

import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from paper.registro import Registro

# La clave que lee el panel. Si cambia, cambia también en historialPapel.ts.
CLAVE = "papel:historial"

# Cuántas operaciones cerradas viajan. El panel las muestra todas y el valor de
# una operación vieja es el mismo que el de una nueva —el criterio de aborto se
# mide sobre la serie entera— así que el tope está por el tamaño del valor en
# Redis, no por relevancia. 500 operaciones con su contexto rondan los 700 KB.
TOPE = 500


def _json(texto: str | None) -> Any:
    """El contexto guardado, o None si no se puede leer.

    Se guarda como JSON pero se lee sin confiar: una fila corrupta no debería
    tumbar la publicación entera del historial.
    """
    if not texto:
        return None
    try:
        return json.loads(texto)
    except (ValueError, TypeError):
        return None


def _operacion(fila: dict[str, Any], tramos: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Una fila de SQLite como la espera el panel."""
    return {
        "id": fila["id"],
        "eje": fila["eje"],
        "simbolo": fila["simbolo"],
        "direccion": fila["direccion"],
        "abiertaEn": fila["abierta_en"],
        "precioEntrada": fila["precio_entrada"],
        "stopLoss": fila["stop_loss"],
        "takeProfit": fila["take_profit"],
        "razon": fila["razon"],
        "sello": fila["sello"],
        "contexto": _json(fila["contexto"]),
        "cerradaEn": fila["cerrada_en"],
        "precioSalida": fila["precio_salida"],
        "motivoCierre": fila["motivo_cierre"],
        "rMultiplo": fila["r_multiplo"],
        "contextoSalida": _json(fila["contexto_salida"]),
        "analisis": fila["analisis"],
        # El stop movido y los tramos. Sin esto el panel enseña un resultado sin
        # la gestión que lo produjo, que es la mitad de lo que hay que evaluar:
        # una operación que cerró en +1R tomando la mitad en +2R y moviendo el
        # stop a la entrada no se parece en nada a otra que hizo +1R de una.
        "stopActual": fila.get("stop_actual"),
        "notaStop": fila.get("nota_stop"),
        "tramos": [
            {
                "salidaEn": t["salida_en"],
                "precioSalida": t["precio_salida"],
                "fraccion": t["fraccion"],
                "motivo": t["motivo"],
                "rMultiplo": t["r_multiplo"],
                "analisis": t["analisis"],
            }
            for t in (tramos or [])
        ],
    }


def instantanea(registro: Registro, tope: int = TOPE) -> dict[str, Any]:
    """Lo que el panel necesita para dibujar el historial entero.

    Incluye `sellosRotos` a propósito. Es el dato que decide si el resto vale
    algo: un historial con un sello roto no es "un historial con un problemita",
    es un historial del que no se puede saber si las razones se escribieron
    antes o después de ver el resultado. El panel lo enseña arriba del todo.
    """
    con = registro._con  # noqa: SLF001 — mismo paquete; el módulo es su vecino
    cerradas = [
        _operacion(dict(f), registro.tramos(int(f["id"])))
        for f in con.execute(
            "SELECT * FROM operaciones WHERE cerrada_en IS NOT NULL ORDER BY id DESC LIMIT ?",
            (tope,),
        )
    ]
    return {
        "publicadoEn": datetime.now(UTC).isoformat(),
        # Sin ordenar por resultado, igual que `por_eje`: ordenar esta tabla
        # invita a elegir el mejor eje mirándola, que es el sobreajuste que
        # CRITERIO_ABORTO.md prohíbe.
        "porEje": registro.por_eje(),
        "abiertas": [_operacion(f, registro.tramos(int(f["id"]))) for f in registro.abiertas()],
        # Ascendente: leer un historial es leerlo en orden, aunque se recorten
        # las más viejas.
        "cerradas": list(reversed(cerradas)),
        "sellosRotos": registro.verificar_sellos(),
    }


def publicar(registro: Registro, url: str = "", token: str = "") -> dict[str, Any]:
    """Empuja la foto al panel. Devuelve la foto publicada.

    La URL y el token salen del entorno (`PANEL_URL`, `PANEL_TOKEN`) para que el
    Codespace los reciba como secretos y no vivan en el repo. Sin URL no falla:
    devuelve la foto igual, que es lo que hace falta para probar esto sin red.
    """
    foto = instantanea(registro)
    destino = url or os.environ.get("PANEL_URL", "")
    if not destino:
        return foto

    cuerpo = json.dumps(foto, ensure_ascii=False).encode("utf-8")
    pedido = urllib.request.Request(  # noqa: S310 — destino fijado por entorno
        destino.rstrip("/") + "/api/papel/historial",
        data=cuerpo,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token or os.environ.get('PANEL_TOKEN', '')}",
        },
    )
    with urllib.request.urlopen(pedido, timeout=20) as r:  # noqa: S310
        r.read()
    return foto
