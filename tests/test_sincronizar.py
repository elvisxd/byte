"""Sincronizar el CV en una máquina que no es la de siempre.

Las rutas de destino —el portfolio, Google Drive— salen de variables de entorno
porque llevan el nombre de usuario de quien lo corre. La consecuencia es que
casi siempre falta alguna: en el cron, en una máquina nueva, o simplemente el
día que Drive no está montado. Un destino ausente es el caso normal, no el raro.
"""

import importlib
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def sincronizar(monkeypatch: pytest.MonkeyPatch):
    """El módulo releído sin ninguna ruta opcional configurada.

    Las rutas se resuelven al importar, así que no alcanza con borrar la
    variable: hay que recargar para que el módulo las vuelva a mirar.
    """
    for variable in ("BYTE_PORTFOLIO_DIR", "BYTE_DRIVE_CV_DIR"):
        monkeypatch.delenv(variable, raising=False)
    import perfil.sincronizar as modulo

    return importlib.reload(modulo)


def test_sin_drive_configurado_los_pdf_se_generan_igual(
    sincronizar, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin esto, imprimir explota a la mitad con un AttributeError y el CV en
    español nunca se genera: el inglés queda nuevo y el español viejo, que es
    justo la desincronización que todo este archivo viene a evitar.

    Y explota *después* de haber corregido los HTML, así que reintentar tampoco
    avisa: la segunda corrida dice que ya está todo al día.
    """
    assert sincronizar.DRIVE is None

    generados: list[Path] = []

    def falso_chrome(orden, **kwargs):
        destino = Path(next(a for a in orden if a.startswith("--print-to-pdf=")).split("=", 1)[1])
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"%PDF-1.4" + b"\0" * 20_000)
        generados.append(destino)
        return subprocess.CompletedProcess(orden, 0, "", "")

    monkeypatch.setattr(sincronizar.subprocess, "run", falso_chrome)
    monkeypatch.setattr(sincronizar, "CHROME", tmp_path / "chrome")
    (tmp_path / "chrome").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(sincronizar, "CV_DIR", tmp_path)
    html = tmp_path / "build"
    html.mkdir()
    rutas = []
    for nombre in ("cv-en.html", "cv-es.html"):
        ruta = html / nombre
        ruta.write_text("<html>459 tests</html>", encoding="utf-8")
        rutas.append(ruta)
    monkeypatch.setattr(sincronizar, "HTMLS", rutas)

    assert sincronizar.imprimir() is True
    assert len(generados) == 2, "los dos idiomas, no solo el primero"
