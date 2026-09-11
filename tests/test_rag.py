"""Chunking, parseo y compactación del RAG.

Acá va lo que se puede verificar sin base. `rag/store.py` y la búsqueda híbrida
necesitan pgvector y hoy **no tienen tests**: se verificaron a mano contra un
Postgres real. Es una deuda conocida, no una cobertura delegada a otro archivo.
"""

import io

import pytest

from rag.chunking import CHUNK_CHARS, split
from rag.parsing import ParseError, parse

# --- Chunking ---


def test_texto_vacio_no_genera_chunks() -> None:
    """Un documento en blanco no se indexa: el llamador lo marca como error."""
    assert split("") == []
    assert split("   \n\t  ") == []


def test_texto_corto_entra_en_un_chunk() -> None:
    chunks = split("Hola mundo.")
    assert len(chunks) == 1
    assert chunks[0].content == "Hola mundo."
    assert chunks[0].index == 0


def test_los_chunks_se_solapan() -> None:
    """Sin solapamiento, lo que cae justo en el corte no lo encuentra ninguna
    búsqueda."""
    texto = " ".join(f"palabra{i}" for i in range(3000))
    chunks = split(texto)

    assert len(chunks) > 1
    assert [c.index for c in chunks] == list(range(len(chunks)))
    # El final de un chunk tiene que reaparecer al principio del siguiente.
    assert chunks[0].content[-100:] in chunks[1].content


def test_no_se_cuelga_sin_separadores() -> None:
    """Una palabra larguísima (o un binario mal parseado) no tiene dónde cortar:
    igual tiene que terminar."""
    chunks = split("x" * (CHUNK_CHARS * 3))
    assert len(chunks) >= 3
    assert all(len(c.content) <= CHUNK_CHARS for c in chunks)


def test_corta_en_limites_de_palabra() -> None:
    texto = ("palabra " * 400).strip()
    chunks = split(texto)
    assert len(chunks) > 1
    # Ningún chunk arranca o termina cortando "palabra" al medio.
    for chunk in chunks:
        assert not chunk.content.startswith("abra")
        assert not chunk.content.endswith("pala")


def test_el_texto_completo_sobrevive_al_chunkeo() -> None:
    """Ninguna palabra puede perderse entre dos chunks.

    Antes esto afirmaba `0 < OVERLAP_CHARS < CHUNK_CHARS`, que es cierto por
    definición de dos constantes y no ejecuta `split()`.
    """
    palabras = [f"palabra{i}" for i in range(2000)]
    chunks = split(" ".join(palabras))

    unidas = " ".join(c.content for c in chunks)
    faltantes = [p for p in palabras if p not in unidas]
    assert not faltantes, f"se perdieron {len(faltantes)} palabras al chunkear"


# --- Parseo ---


async def test_texto_plano_y_markdown() -> None:
    plano = await parse(b"Hola mundo", "notas.txt", 5.0)
    assert plano.text == "Hola mundo"
    assert plano.mime_type == "text/plain"

    md = await parse(b"# Titulo", "doc.md", 5.0)
    assert md.mime_type == "text/markdown"


async def test_archivo_vacio_se_rechaza() -> None:
    with pytest.raises(ParseError):
        await parse(b"", "vacio.txt", 5.0)
    with pytest.raises(ParseError):
        await parse(b"   \n  ", "espacios.txt", 5.0)


async def test_binario_no_se_indexa_como_texto() -> None:
    """Un .zip renombrado no debe terminar indexado como si fuera texto."""
    with pytest.raises(ParseError, match="no es texto ni PDF"):
        await parse(b"PK\x03\x04\x00\x00basura", "falso.txt", 5.0)


async def test_utf8_roto_se_indexa_igual() -> None:
    """Unos bytes raros no deberían tirar abajo el documento entero."""
    parsed = await parse("café".encode() + b"\xff\xfe", "raro.txt", 5.0)
    assert "café" in parsed.text


async def test_el_tipo_sale_del_contenido_no_de_la_extension() -> None:
    """Magic bytes, como pide el contrato: un texto con nombre .pdf es texto."""
    parsed = await parse(b"esto es texto plano", "mentira.pdf", 5.0)
    assert parsed.mime_type == "text/plain"


async def test_pdf_roto_da_error_entendible() -> None:
    with pytest.raises(ParseError):
        await parse(b"%PDF-1.4 y nada mas", "roto.pdf", 5.0)


async def test_pdf_sin_texto_avisa_que_puede_ser_escaneo() -> None:
    """Un PDF de imágenes no tiene nada que indexar: mejor decirlo que dejarlo
    'indexado' con cero chunks."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)

    with pytest.raises(ParseError, match="escaneo"):
        await parse(buffer.getvalue(), "escaneo.pdf", 5.0)


# --- Compactación del historial ---


class LlmQueResume:
    """Doble del modelo: devuelve lo que se le diga, y anota qué le pidieron."""

    def __init__(self, *respuestas: str) -> None:
        self._respuestas = list(respuestas)
        self.pedidos: list[str] = []

    async def ainvoke(self, messages: list[object]) -> object:
        from langchain_core.messages import AIMessage

        self.pedidos.append(str(messages[-1].content))  # type: ignore[attr-defined]
        return AIMessage(content=self._respuestas.pop(0) if self._respuestas else "resumen")


def _conversacion() -> list[object]:
    from langchain_core.messages import AIMessage, HumanMessage

    return [HumanMessage(content="Elegí Postgres con pgvector"), AIMessage(content="Buena idea")]


async def test_el_resumen_previo_no_se_pierde() -> None:
    """El modelo resume solo lo nuevo y se concatena: pidiéndole que integre los
    dos, un 8B se queda con lo reciente y descarta lo viejo."""
    from agent.compact import resumir

    llm = LlmQueResume("Eligió pgvector")
    resultado = await resumir(llm, _conversacion(), previo="Se llama Elvis")

    assert resultado is not None
    assert "Se llama Elvis" in resultado
    assert "Eligió pgvector" in resultado
    # Al modelo no se le pasa el resumen previo: no puede tirarlo.
    assert "Se llama Elvis" not in llm.pedidos[0]


async def test_sin_resumen_nuevo_se_conserva_el_previo() -> None:
    """Si el modelo falla, perder el resumen que ya había sería peor."""
    from agent.compact import resumir

    class LlmCaido:
        async def ainvoke(self, messages: list[object]) -> object:
            raise RuntimeError("ollama no responde")

    assert await resumir(LlmCaido(), _conversacion(), previo="lo de antes") == "lo de antes"


async def test_sin_mensajes_no_se_llama_al_modelo() -> None:
    from agent.compact import resumir

    llm = LlmQueResume()
    assert await resumir(llm, [], previo="lo de antes") == "lo de antes"
    assert llm.pedidos == []


async def test_el_resumen_se_recomprime_cuando_no_entra() -> None:
    """Recién cuando el acumulado pasa el tope se vuelve a resumir todo junto."""
    from agent.compact import MAX_SUMMARY_CHARS, resumir

    llm = LlmQueResume("x" * 500, "condensado")
    resultado = await resumir(llm, _conversacion(), previo="y" * MAX_SUMMARY_CHARS)

    assert resultado == "condensado"
    assert len(llm.pedidos) == 2  # el resumen nuevo, y después la recompresión


# --- Embeddings: los errores de Ollama son el fallo operativo más común ---


class _RespuestaFalsa:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _ClienteFalso:
    """Reemplaza a httpx.AsyncClient dentro de Embedder.embed."""

    def __init__(self, payload: dict | None = None, error: Exception | None = None) -> None:
        self._payload = payload or {}
        self._error = error

    async def __aenter__(self) -> "_ClienteFalso":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, *_: object, **__: object) -> _RespuestaFalsa:
        if self._error is not None:
            raise self._error
        return _RespuestaFalsa(self._payload)


async def test_sin_textos_no_se_llama_a_ollama() -> None:
    from rag.embeddings import Embedder

    assert await Embedder("http://x", "m").embed([]) == []


async def test_ollama_caido_da_embedding_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Es el fallo más común en la práctica: el servicio no está levantado."""
    import httpx

    from rag.embeddings import Embedder, EmbeddingError

    monkeypatch.setattr(
        "httpx.AsyncClient", lambda **_: _ClienteFalso(error=httpx.ConnectError("sin ruta"))
    )
    with pytest.raises(EmbeddingError):
        await Embedder("http://x", "m").embed(["hola"])


async def test_vector_de_otra_dimension_se_rechaza(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cambiar OLLAMA_EMBED_MODEL sin migrar la columna corrompería la tabla:
    mejor fallar acá, con el motivo claro."""
    from rag.embeddings import Embedder, EmbeddingError

    monkeypatch.setattr(
        "httpx.AsyncClient", lambda **_: _ClienteFalso({"embeddings": [[0.1, 0.2, 0.3]]})
    )
    with pytest.raises(EmbeddingError, match="768"):
        await Embedder("http://x", "m").embed(["hola"])


async def test_faltan_vectores_se_rechaza(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un lote parcial desalinearía los chunks de sus vectores."""
    from rag.embeddings import EMBEDDING_DIM, Embedder, EmbeddingError

    monkeypatch.setattr(
        "httpx.AsyncClient", lambda **_: _ClienteFalso({"embeddings": [[0.0] * EMBEDDING_DIM]})
    )
    with pytest.raises(EmbeddingError, match="se pidieron 2"):
        await Embedder("http://x", "m").embed(["uno", "dos"])


# --- Ingesta: todo termina en indexed o error, nunca propaga ---


class _StoreFalso:
    def __init__(self) -> None:
        self.guardados: list[tuple] = []
        self.errores: list[str] = []

    async def save_chunks(self, document_id: str, chunks: list) -> None:
        self.guardados.append((document_id, chunks))

    async def mark_error(self, document_id: str, mensaje: str) -> None:
        self.errores.append(mensaje)


class _EmbedderFalso:
    def __init__(self, dim: int = 768, error: Exception | None = None) -> None:
        self._dim = dim
        self._error = error

    async def embed(self, textos: list[str]) -> list[list[float]]:
        if self._error is not None:
            raise self._error
        return [[0.1] * self._dim for _ in textos]


def _ingestor(store: _StoreFalso, embedder: object, batch: int = 2) -> object:
    from rag.ingest import Ingestor

    return Ingestor(store=store, embedder=embedder, parse_timeout_s=5.0, batch_size=batch)


async def test_un_archivo_ilegible_queda_en_error() -> None:
    """El usuario tiene que ver el motivo, no un documento colgado."""
    store = _StoreFalso()
    await _ingestor(store, _EmbedderFalso()).index("doc-1", b"PK\x03\x04\x00\x00", "raro.zip")

    assert store.guardados == []
    assert store.errores and "no es texto ni PDF" in store.errores[0]


async def test_si_fallan_los_embeddings_el_documento_queda_en_error() -> None:
    from rag.embeddings import EmbeddingError

    store = _StoreFalso()
    embedder = _EmbedderFalso(error=EmbeddingError("ollama no responde"))
    await _ingestor(store, embedder).index("doc-2", b"texto suficiente", "notas.txt")

    assert store.guardados == []
    assert store.errores and "ollama" in store.errores[0]


async def test_el_indexado_vectoriza_en_lotes() -> None:
    """Un documento grande en una sola llamada se pasa del timeout de Ollama."""
    store = _StoreFalso()
    embedder = _EmbedderFalso()
    texto = " ".join(f"palabra{i}" for i in range(3000))
    await _ingestor(store, embedder, batch=2).index("doc-3", texto.encode(), "largo.txt")

    assert store.errores == []
    assert len(store.guardados) == 1
    _, chunks = store.guardados[0]
    assert len(chunks) > 2
    # Cada chunk sale con su índice y un vector de la dimensión correcta.
    assert [c[0] for c in chunks] == list(range(len(chunks)))
    assert all(len(c[2]) == 768 for c in chunks)


async def test_un_error_inesperado_no_tumba_la_tarea() -> None:
    """Corre en segundo plano: si escapara una excepción, nadie la vería."""
    store = _StoreFalso()

    class _EmbedderRoto:
        async def embed(self, _textos: list[str]) -> list[list[float]]:
            raise RuntimeError("algo raro")

    await _ingestor(store, _EmbedderRoto()).index("doc-4", b"texto valido", "notas.txt")
    assert store.errores and "inesperado" in store.errores[0]
