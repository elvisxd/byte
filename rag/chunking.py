"""Partido de documentos en chunks para indexar.

~500 tokens por chunk con 50 de solapamiento (docs/plan-asistente-ia-local.md).
El solapamiento existe para que una frase partida al medio siga siendo
recuperable: sin él, lo que cae justo en el corte no aparece en ninguna búsqueda.
"""

from dataclasses import dataclass

# Misma aproximación que agent/graph.py: sin tokenizer, 4 caracteres por token.
# Alcanza para dimensionar chunks, y evita cargar un tokenizer por documento.
CHARS_PER_TOKEN = 4
CHUNK_TOKENS = 500
OVERLAP_TOKENS = 50

CHUNK_CHARS = CHUNK_TOKENS * CHARS_PER_TOKEN
OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    content: str


def _corte_limpio(texto: str, desde: int, hasta: int) -> int:
    """Dónde terminar el chunk para no partir una palabra o un párrafo al medio.

    Busca hacia atrás el mejor corte disponible dentro del último tramo: primero
    un fin de párrafo, después un fin de línea, después un espacio. Si no hay
    ninguno (una palabra larguísima, un binario mal parseado), corta duro.
    """
    if hasta >= len(texto):
        return len(texto)

    # Solo se retrocede sobre el último cuarto del chunk: más atrás genera chunks
    # demasiado chicos y multiplica la cantidad de embeddings.
    minimo = desde + (hasta - desde) * 3 // 4
    for separador in ("\n\n", "\n", " "):
        corte = texto.rfind(separador, minimo, hasta)
        if corte != -1:
            return corte + len(separador)
    return hasta


def split(texto: str) -> list[Chunk]:
    """Parte el texto en chunks solapados.

    Devuelve lista vacía si no hay nada que indexar: un documento en blanco no
    genera chunks (y el llamador lo marca como error, no como indexado).
    """
    texto = texto.strip()
    if not texto:
        return []

    chunks: list[Chunk] = []
    inicio = 0
    while inicio < len(texto):
        fin = _corte_limpio(texto, inicio, inicio + CHUNK_CHARS)
        contenido = texto[inicio:fin].strip()
        if contenido:
            chunks.append(Chunk(index=len(chunks), content=contenido))

        if fin >= len(texto):
            break
        # El siguiente arranca un poco antes para solapar. El max() protege el
        # caso patológico de un corte tan corto que el inicio no avanzaría nunca.
        inicio = max(fin - OVERLAP_CHARS, inicio + 1)

    return chunks
