"""Extracción de texto de los documentos que se suben.

El tipo se decide por el contenido, no por la extensión ni por el Content-Type
que manda el cliente (docs/api-contrato-byte.md: "validados por magic bytes").
Un .txt renombrado a .pdf no debe llegar al parser de PDF.
"""

import asyncio
from dataclasses import dataclass

from api.logging import get_logger

logger = get_logger("rag.parsing")

PDF_MAGIC = b"%PDF-"
# Un PDF entero en memoria ya está acotado por max_document_bytes; esto acota el
# texto extraído, que puede ser mucho mayor que el archivo original.
MAX_TEXT_CHARS = 2_000_000


class ParseError(ValueError):
    """El archivo no se pudo leer como texto indexable."""


@dataclass(frozen=True, slots=True)
class Parsed:
    text: str
    mime_type: str


def _es_pdf(data: bytes) -> bool:
    return data[:5] == PDF_MAGIC


def _texto_de_pdf(data: bytes) -> str:
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    # Un PDF con contraseña no se puede leer y no tiene sentido reintentarlo:
    # se rechaza con un motivo entendible en vez de devolver texto vacío.
    if reader.is_encrypted:
        raise ParseError("el PDF está protegido con contraseña")

    partes = [pagina.extract_text() or "" for pagina in reader.pages]
    texto = "\n\n".join(parte for parte in partes if parte.strip())
    if not texto.strip():
        # Típico de PDFs escaneados: son imágenes, y sin OCR no hay nada que
        # indexar. Mejor decirlo que dejar un documento "indexado" con 0 chunks.
        raise ParseError("el PDF no tiene texto extraíble (¿es un escaneo?)")
    return texto


def _texto_plano(data: bytes) -> str:
    # utf-8 con reemplazo: un documento con unos bytes raros se indexa igual,
    # en vez de fallar entero por un caracter.
    texto = data.decode("utf-8", errors="replace")
    if not texto.strip():
        raise ParseError("el archivo está vacío")
    return texto


def _parse_sync(data: bytes, filename: str) -> Parsed:
    if _es_pdf(data):
        return Parsed(text=_texto_de_pdf(data)[:MAX_TEXT_CHARS], mime_type="application/pdf")

    if b"\x00" in data[:8192]:
        # Heurística de binario: los formatos de texto no traen bytes nulos.
        # Evita indexar un .zip o una imagen renombrada como si fuera texto.
        raise ParseError("el archivo no es texto ni PDF")

    mime = "text/markdown" if filename.lower().endswith((".md", ".markdown")) else "text/plain"
    return Parsed(text=_texto_plano(data)[:MAX_TEXT_CHARS], mime_type=mime)


async def parse(data: bytes, filename: str, timeout_s: float) -> Parsed:
    """Extrae el texto sin bloquear el event loop ni colgarse.

    pypdf es sincrónico y con un PDF patológico puede tardar muchísimo, así que
    corre en un hilo y con plazo: vencido el plazo, el documento queda en error.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_parse_sync, data, filename), timeout=timeout_s
        )
    except TimeoutError as exc:
        logger.warning("parseo_timeout", filename=filename, timeout_s=timeout_s)
        raise ParseError(f"el parseo tardó más de {timeout_s:.0f}s") from exc
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf tira de todo con archivos rotos
        logger.warning("parseo_fallo", filename=filename, error_type=type(exc).__name__)
        raise ParseError(f"no se pudo leer el archivo: {type(exc).__name__}") from exc
