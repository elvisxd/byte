"""Redacción de secretos y PII antes de que algo salga hacia un tercero.

Lo usan Langfuse (trazas del agente) y Bugsink (errores). Los dos reciben texto
que el usuario escribió, que el modelo generó o que una herramienta devolvió, y
ninguno corre en tu máquina: lo que se les manda, se fue.

El checklist (`docs/seguridad-byte.md`, Fase 4) pide redactar secretos y PII
antes de enviar. Esto no es un antivirus: es una red que atrapa lo que aparece
de verdad en una conversación con un agente —una clave pegada para que la use,
un email en una consulta, el número de tarjeta de un ticket— y lo reemplaza por
una marca que dice qué era.

Dos reglas que explican las decisiones de abajo:

- **Se prefiere redactar de más.** Un falso positivo arruina una traza; un falso
  negativo filtra una credencial a un servicio de terceros.
- **La marca dice qué se redactó**, no solo que algo se redactó. `[API_KEY]` en
  una traza explica por qué el modelo falló después; un `***` no explica nada.
"""

import re
from typing import Any

# Las claves primero: son las que más daño hacen y las más reconocibles. El
# orden importa, porque un token de OpenAI también contiene algo que parece una
# palabra larga y no queremos que otra regla lo parta antes.
_PATRONES: list[tuple[str, re.Pattern[str]]] = [
    # Claves con prefijo conocido. `sk-` es OpenAI, `ghp_`/`gho_` GitHub,
    # `xoxb-` Slack, `AKIA` AWS, `AIza` Google, `pk-lf`/`sk-lf` Langfuse.
    #
    # Los largos van con mínimo y no exactos: un formato rígido es un falso
    # negativo esperando a que el proveedor cambie un carácter, y acá un falso
    # negativo es una credencial filtrada.
    (
        "API_KEY",
        re.compile(
            r"\b(?:sk|pk)-(?:lf-)?[A-Za-z0-9_-]{16,}"
            r"|\bgh[pousr]_[A-Za-z0-9]{16,}"
            r"|\bxox[baprs]-[A-Za-z0-9-]{10,}"
            r"|\bAKIA[0-9A-Z]{16}\b"
            r"|\bAIza[0-9A-Za-z_-]{30,}"
        ),
    ),
    # `Authorization: Bearer <algo>` y `api_key=<algo>`: el valor, no el nombre.
    (
        "CREDENCIAL",
        re.compile(
            r"(?i)\b(?:bearer|token|api[_-]?key|secret|password|passwd|pwd)"
            r"\s*[:=]\s*[\"']?([A-Za-z0-9._~+/=-]{8,})[\"']?"
        ),
    ),
    # Un JWT se reconoce por su forma: tres bloques base64url con puntos.
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    # Una URL con credenciales adentro (postgres://usuario:clave@host).
    ("DSN", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s/@]+@[^\s]+")),
    # Tarjetas: 13-19 dígitos con espacios o guiones opcionales. Se valida con
    # Luhn para no redactar cualquier número largo (un id, un timestamp).
    ("TARJETA", re.compile(r"\b\d(?:[ -]?\d){12,18}\b")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
]

# Cuánto texto se manda como mucho. Una traza no necesita el documento entero,
# y un cuerpo enorme es una forma cara de filtrar datos sin darse cuenta.
MAX_CHARS = 4000


def _luhn(digitos: str) -> bool:
    """El dígito verificador de una tarjeta.

    Sin esto, cualquier número largo —un id, un hash numérico, un timestamp en
    milisegundos repetido— se redactaría como si fuera una tarjeta.
    """
    total, alterno = 0, False
    for caracter in reversed(digitos):
        valor = int(caracter)
        if alterno:
            valor *= 2
            if valor > 9:
                valor -= 9
        total += valor
        alterno = not alterno
    return total % 10 == 0


def _redactar_tarjeta(match: re.Match[str]) -> str:
    digitos = re.sub(r"[ -]", "", match.group(0))
    if 13 <= len(digitos) <= 19 and _luhn(digitos):
        return "[TARJETA]"
    return match.group(0)


def redactar(texto: str) -> str:
    """El texto con los secretos y la PII reemplazados por su marca."""
    if not texto:
        return texto
    for etiqueta, patron in _PATRONES:
        if etiqueta == "TARJETA":
            texto = patron.sub(_redactar_tarjeta, texto)
        elif etiqueta == "CREDENCIAL":
            # Solo el valor: `Authorization: Bearer [CREDENCIAL]` sigue diciendo
            # qué header era, que es lo que sirve para depurar.
            texto = patron.sub(lambda m: m.group(0).replace(m.group(1), "[CREDENCIAL]"), texto)
        else:
            texto = patron.sub(f"[{etiqueta}]", texto)
    return texto


def redactar_dato(dato: Any, _profundidad: int = 0) -> Any:
    """Redacta recursivamente lo que va a salir: texto, listas y diccionarios.

    Es lo que recibe el `mask` de Langfuse, que le pasa el input, el output y la
    metadata de cada observación. Se acota el texto además de redactarlo: una
    traza no necesita el documento entero.
    """
    if _profundidad > 8:
        # Una estructura demasiado anidada es más probable que sea un error que
        # algo que valga la pena mandar.
        return "[DEMASIADO_ANIDADO]"
    if isinstance(dato, str):
        redactado = redactar(dato)
        if len(redactado) > MAX_CHARS:
            return redactado[:MAX_CHARS] + f"… [recortado, {len(redactado) - MAX_CHARS} más]"
        return redactado
    if isinstance(dato, dict):
        return {k: redactar_dato(v, _profundidad + 1) for k, v in dato.items()}
    if isinstance(dato, (list, tuple)):
        return [redactar_dato(v, _profundidad + 1) for v in dato]
    return dato


def mask_langfuse(*, data: Any, **_kwargs: Any) -> Any:
    """La firma que espera el `mask` de Langfuse.

    Se pasa al constructor del cliente, así que corre sobre **todo** lo que el
    SDK está por mandar: el input y el output de cada observación, y su
    metadata. Ponerlo acá y no en cada punto de instrumentación es lo que hace
    que no dependa de acordarse.
    """
    return redactar_dato(data)
