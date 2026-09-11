"""Chunking, parseo e ingesta del RAG.

El store y la búsqueda híbrida necesitan pgvector, así que se prueban contra el
Postgres real (tests/test_postgres.py corre solo si hay DSN); acá va lo que se
puede verificar sin base.
"""

import io

import pytest

from rag.chunking import CHUNK_CHARS, OVERLAP_CHARS, split
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


def test_el_solapamiento_es_menor_que_el_chunk() -> None:
    """Si el solapamiento fuera >= al chunk, el índice no avanzaría nunca."""
    assert 0 < OVERLAP_CHARS < CHUNK_CHARS


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
