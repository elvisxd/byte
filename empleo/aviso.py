"""Mandar el aviso al teléfono.

Va por el mismo camino que el vigía de `paper/`: un POST al panel, que es quien
tiene el token del bot de Telegram. La Mac no lo tiene a propósito —un secreto
menos en la máquina de uso diario— y ese camino ya está probado en producción,
así que no se inventa uno nuevo para esto.

Best-effort: si el panel no contesta, el cazador sigue. El digest queda escrito
en disco igual, y perder un aviso no puede costar las ofertas del día.
"""

import json
import os
import urllib.error
import urllib.request

from api.logging import get_logger

logger = get_logger("empleo.aviso")

TIMEOUT_S = 10

# Lo que entra en UN mensaje. El límite duro es el de `sendMessage` de Telegram
# —4096— y desde el PR del panel esa es también la validación de la ruta, que
# antes cortaba en 1000 y devolvía 422 con el aviso entero.
#
# Se deja aire por dos motivos: la marca de parte («(2/3)») se agrega después
# de medir, y el panel valida sobre el texto ya pasado por `trim()`, que no
# tiene por qué contar exactamente igual que acá.
MAX_CARACTERES = 3900

# Lo que se reserva de cada parte para la marca y, en la última, para la nota
# de que algo quedó afuera. Ambas se agregan cuando el corte ya está hecho.
RESERVA_DE_MARCA = 80

# Cuántos mensajes se mandan como mucho de un solo aviso.
#
# No es un tope de largo: con cuatro ofertas por aviso el mensaje mide ~1.300
# caracteres y nunca se parte. Es un freno a un error nuestro —un feed que
# devuelve mil títulos, un pie que se va de mano— para que el teléfono no
# reciba veinte mensajes seguidos a las 9 de la mañana. Seis partes son ~23.000
# caracteres: mucho más de lo que un aviso sano puede medir.
TOPE_DE_PARTES = 6


def _cortar_parrafo(parrafo: str, tope: int) -> list[str]:
    """Un párrafo más largo que el tope, partido lo más tarde posible.

    No debería pasar: un párrafo del aviso es una oferta, y una oferta es
    título, empresa, lugar y link. Pasa igual el día que una fuente devuelve un
    título de mil caracteres, y entonces esto es lo que evita que el corte por
    párrafos entre en un bucle o mande una parte que el panel rechaza.
    """
    if len(parrafo) <= tope:
        return [parrafo]
    pedazos: list[str] = []
    resto = parrafo
    while len(resto) > tope:
        # Se busca el último salto de línea que entre; si el párrafo no tiene
        # ninguno, se corta a lo bruto. Cortar por el medio de una línea es
        # feo, pero perderla es peor.
        corte = resto.rfind("\n", 0, tope + 1)
        if corte <= 0:
            corte = tope
        pedazos.append(resto[:corte].rstrip())
        resto = resto[corte:].lstrip("\n")
    if resto:
        pedazos.append(resto)
    return pedazos


def _agrupar(texto: str, tope: int) -> list[str]:
    """El texto en trozos de hasta `tope`, cortando entre párrafos.

    Se corta entre párrafos y no cada N caracteres porque cada párrafo del
    aviso es una oferta entera —puntaje, título, lugar y link—: partirla al
    medio deja media oferta en un mensaje y un link suelto en el siguiente.
    """
    trozos: list[str] = []
    actual = ""
    for parrafo in texto.split("\n\n"):
        for pedazo in _cortar_parrafo(parrafo, tope):
            if not actual:
                actual = pedazo
            elif len(actual) + 2 + len(pedazo) <= tope:
                actual = f"{actual}\n\n{pedazo}"
            else:
                trozos.append(actual)
                actual = pedazo
    if actual:
        trozos.append(actual)
    return trozos


def partir(texto: str, tope: int = MAX_CARACTERES) -> list[str]:
    """El aviso en las partes que hagan falta, sin perder nada por el camino.

    Antes esto recortaba: lo que pasaba del tope se tiraba y se agregaba una
    nota. Como el pie —el conteo de fuentes, que es donde se ve si LinkedIn o
    Job Bank se cayeron— va al final, lo primero que se perdía era justo la
    señal de que algo andaba mal.

    Un aviso que entra en un mensaje se manda tal cual, sin marca: la marca
    sólo aparece cuando de verdad hay más de una parte, así que el caso normal
    —cuatro ofertas, ~1.300 caracteres— se lee igual que siempre.
    """
    if len(texto) <= tope:
        return [texto]

    trozos = _agrupar(texto, tope - RESERVA_DE_MARCA)
    if len(trozos) > TOPE_DE_PARTES:
        sobran = len(trozos) - TOPE_DE_PARTES
        trozos = trozos[:TOPE_DE_PARTES]
        trozos[-1] += f"\n[...{sobran} parte(s) más, en el digest]"
    total = len(trozos)
    return [f"{trozo}\n\n({numero}/{total})" for numero, trozo in enumerate(trozos, 1)]


def _mandar(destino: str, texto: str) -> bool:
    pedido = urllib.request.Request(  # noqa: S310 - destino fijado por entorno, no por el modelo
        destino.rstrip("/") + "/api/papel/aviso",
        data=json.dumps({"texto": texto}, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('PANEL_TOKEN', '')}",
        },
    )
    try:
        with urllib.request.urlopen(pedido, timeout=TIMEOUT_S) as respuesta:  # noqa: S310
            respuesta.read()
    except (OSError, urllib.error.URLError, ValueError) as exc:
        logger.warning("aviso_fallo", error_type=type(exc).__name__)
        return False
    return True


def avisar(texto: str) -> bool:
    """Manda el texto al panel, que lo reenvía a Telegram. Nunca lanza.

    Devuelve `True` sólo si llegaron **todas** las partes. El cazador anota en
    la memoria lo que avisó, así que un `False` significa que esas ofertas
    vuelven en la próxima vuelta: con dos partes de tres entregadas eso repite
    algunas, y repetir es mucho más barato que dejar una oferta sin avisar.

    Si una parte falla, las demás se mandan igual. Que el panel se caiga entre
    la primera y la segunda no tiene por qué costar la tercera, y la marca de
    parte deja el hueco a la vista en el teléfono.
    """
    destino = os.environ.get("PANEL_URL", "")
    if not destino:
        logger.info("aviso_sin_panel", detail="PANEL_URL vacío: el digest queda solo en disco")
        return False

    partes = partir(texto)
    entregadas = sum(1 for parte in partes if _mandar(destino, parte))
    if entregadas != len(partes):
        logger.warning("aviso_incompleto", entregadas=entregadas, partes=len(partes))
    return entregadas == len(partes)
