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
            # Stripe (`sk_live_`, `rk_live_`, y sus variantes de prueba),
            # GitHub fine-grained, HuggingFace, SendGrid, npm y Twilio.
            r"|\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"
            r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
            r"|\bhf_[A-Za-z0-9]{30,}"
            r"|\bSG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"
            r"|\bnpm_[A-Za-z0-9]{30,}"
            r"|\bSK[0-9a-f]{32}\b"
        ),
    ),
    # Una clave privada en PEM u OpenSSH. Se redacta desde el encabezado hasta
    # el final: el cuerpo es base64 y ningún otro patrón lo reconocería.
    (
        "CLAVE_PRIVADA",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
            r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
        ),
    ),
    # `Authorization: Bearer <algo>`, `api_key=<algo>`, `{"password": "<algo>"}`:
    # el valor, no el nombre.
    #
    # Tres cosas que la primera versión no cubría, y cada una dejaba pasar
    # secretos de verdad:
    # - **El separador puede ser un espacio.** `Bearer <token>` es el formato
    #   HTTP real, y exigir `[:=]` lo dejaba entero afuera.
    # - **Los nombres compuestos.** `\btoken\b` no matchea `access_token`
    #   porque el `_` es carácter de palabra; hace falta permitir el prefijo.
    # - **JSON.** `{"password": "x"}` tiene comillas entre el nombre y el valor,
    #   y es justo el formato de los resultados de herramienta.
    (
        "CREDENCIAL",
        re.compile(
            # El separador va acotado (`[ \t]{0,4}` en vez de `\s*`) y sin
            # solaparse con la clase que lo contiene: `\s*[:=\s]\s*` hacía
            # backtracking cuadrático, y 40 KB de `1-1-1-…` costaban 800 ms.
            #
            # Dos alternativas porque el separador difiere: `nombre: valor` o
            # `nombre=valor` para las claves con nombre, y `Bearer valor` —con
            # espacio y sin dos puntos— para el header HTTP.
            r"(?i)\b(?:[a-z0-9_-]{0,24}(?:token|key|secret|password|passwd|pwd))"
            r"[\"']?[ \t]{0,4}[:=][ \t]{0,4}[\"']?([A-Za-z0-9._~+/=-]{8,})"
            r"|\b(?:[Bb]earer|[Bb]asic)[ \t]{1,4}([A-Za-z0-9._~+/=-]{8,})"
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
    # IBAN: dos letras de país, dos dígitos de control y hasta 30 alfanuméricos.
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    # Un teléfono con prefijo internacional o con separadores. Va **después**
    # de las tarjetas a propósito: un número de tarjeta con guiones también
    # parece un teléfono, y el de arriba ya lo validó con Luhn.
    #
    # No se intenta atrapar cualquier secuencia de 7-8 dígitos sueltos: en una
    # conversación con un agente eso es casi siempre un id, un puerto o un año,
    # y redactarlos arruinaría la traza sin proteger nada.
    (
        "TELEFONO",
        re.compile(r"\+\d[\d ().-]{7,}\d|\b\d{3,4}[ .-]\d{3,4}[ .-]\d{3,4}\b"),
    ),
]

# Cuánto texto se manda como mucho. Una traza no necesita el documento entero,
# y un cuerpo enorme es una forma cara de filtrar datos sin darse cuenta.
MAX_CHARS = 4000

# Cuánto se baja por una estructura antes de cortar. Con 8 se perdía justo lo
# que sirve para depurar: un evento de Sentry es
# `exception > values[] > stacktrace > frames[] > vars > …`, y ahí recién
# empiezan las variables locales. 16 deja ver el frame entero sin que una
# estructura circular o absurda cueste caro.
MAX_PROFUNDIDAD = 16


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
            # Por posición y no con `replace`: si el valor coincide con el
            # nombre (`password=password`), el replace se come también el
            # nombre, que es justo lo que se quiere conservar.
            def _solo_el_valor(m: re.Match[str]) -> str:
                # El patrón tiene dos alternativas, así que solo uno de los dos
                # grupos matchea: se toma el que exista.
                indice = 1 if m.group(1) is not None else 2
                inicio, fin = m.span(indice)
                return (
                    m.group(0)[: inicio - m.start()]
                    + "[CREDENCIAL]"
                    + m.group(0)[fin - m.start() :]
                )

            texto = patron.sub(_solo_el_valor, texto)
        else:
            texto = patron.sub(f"[{etiqueta}]", texto)
    return texto


def redactar_dato(dato: Any, _profundidad: int = 0) -> Any:
    """Redacta recursivamente lo que va a salir: texto, listas y diccionarios.

    Es lo que recibe el `mask` de Langfuse, que le pasa el input, el output y la
    metadata de cada observación. Se acota el texto además de redactarlo: una
    traza no necesita el documento entero.
    """
    if _profundidad > MAX_PROFUNDIDAD:
        # Una estructura más honda que esto es más probable que sea un error
        # que algo que valga la pena mandar.
        return "[DEMASIADO_ANIDADO]"
    if isinstance(dato, str):
        # **Recortar primero, redactar después.** Al revés, el tope no protegía
        # del costo: las regex de email y DSN son cuadráticas, y un texto
        # patológico de 62 KB —que entra cómodo en el cuerpo de un POST— tardaba
        # más de 3 segundos contra los 2,7 ms de prosa normal. Es CPU pura sin
        # `await`, así que bloquea el event loop entero.
        #
        # Se corta con margen (`MAX_CHARS * 2`) y no justo en `MAX_CHARS`: un
        # secreto partido por el corte no lo atraparía ningún patrón, y el
        # segundo recorte —después de redactar— deja el largo final correcto.
        if len(dato) > MAX_CHARS * 2:
            dato = dato[: MAX_CHARS * 2]
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
