"""Mantiene al día el CV: los números, los PDF y la copia del portfolio.

El flujo era todo a mano: corregir dos HTML, abrir cada uno en el navegador,
Imprimir → PDF, y copiar los dos PDF al portfolio. Por eso el CV decía "183
tests" cuando ya eran 459, y por eso había seis copias de distinto tamaño.

**Los PDF salen del mismo Chrome con el que se imprimían a mano**, en modo
headless. No es una herramienta distinta que podría cambiar el diseño: es el
mismo motor, y el PDF generado sale de 590.760 bytes contra los 590.759 del
impreso a mano — un byte de metadata de fecha, mismas 4 páginas y la misma foto.

Esto no reescribe el CV, que es tuyo. Solo los números que envejecen solos.

    uv run python perfil/sincronizar.py           # dice qué está desactualizado
    uv run python perfil/sincronizar.py --aplicar # corrige, imprime y copia
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from perfil.actualizar import todo  # noqa: E402


# Las rutas salen del entorno, no del código: son de la máquina de quien lo usa
# —y llevan su nombre de usuario y su correo—, así que escribirlas acá las
# publicaría junto con el repo.
def _ruta(variable: str, defecto: str = "") -> Path | None:
    valor = os.environ.get(variable, defecto)
    return Path(valor).expanduser() if valor else None


CV_DIR = _ruta("BYTE_CV_DIR", "~/Downloads/cv-elvis") or Path.home()
HTMLS = [CV_DIR / "build" / "cv-en.html", CV_DIR / "build" / "cv-es.html"]
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PORTFOLIO = _ruta("BYTE_PORTFOLIO_DIR")
# Google Drive, que es de donde salen los CV que se mandan desde el teléfono —
# y por eso los que más importa que estén al día. Si no está montado o no está
# configurado, se saltea sin fallar.
DRIVE = _ruta("BYTE_DRIVE_CV_DIR")

# De qué HTML sale cada PDF, y con qué nombre se guarda en cada destino. Los
# nombres difieren entre la carpeta de trabajo y el portfolio, así que se
# declaran en vez de derivarse.
SALIDAS = {
    "cv-en.html": ("Elvis-Pino-CV-en.pdf", "Elvis-Pino-CV.pdf"),
    "cv-es.html": ("Elvis-Pino-CV-es.pdf", "Elvis-Pino-CV-es.pdf"),
}


def _reemplazos(datos: dict[str, object]) -> list[tuple[re.Pattern[str], str, str]]:
    """(patrón, reemplazo, por qué) para cada número que envejece.

    Cada patrón matchea el número **con su unidad alrededor**, no el número
    suelto: cambiar todos los "459" de un HTML rompería un color hexadecimal o
    un tamaño de fuente. Acá se pide "N tests" o "N pruebas" explícitamente.
    """
    tests = datos["tests"]
    return [
        (re.compile(r"\b\d+ tests\b"), f"{tests} tests", "tests (en)"),
        (re.compile(r"\b\d+ pruebas\b"), f"{tests} pruebas", "tests (es)"),
    ]


def revisar(aplicar: bool = False) -> int:
    """Compara y, si se pide, corrige. Devuelve cuántas cosas estaban viejas."""
    if not CV_DIR.is_dir():
        print(f"no encuentro {CV_DIR} — ¿se movió la carpeta del CV?")
        return 0

    datos = todo()
    print(f"números de hoy: {datos['tests']} tests, {datos['commits']} commits\n")

    desactualizados = 0
    for html in HTMLS:
        if not html.is_file():
            print(f"  falta {html.name}")
            continue
        texto = html.read_text(encoding="utf-8")
        original = texto
        cambios: list[str] = []
        for patron, nuevo, que in _reemplazos(datos):
            encontrados = {m.group(0) for m in patron.finditer(texto)}
            viejos = {e for e in encontrados if e != nuevo}
            if viejos:
                cambios.append(f"{que}: {', '.join(sorted(viejos))} → {nuevo}")
                texto = patron.sub(nuevo, texto)
        if not cambios:
            print(f"  ✓ {html.name} al día")
            continue
        desactualizados += len(cambios)
        marca = "corregido" if aplicar else "desactualizado"
        print(f"  {html.name} — {marca}")
        for c in cambios:
            print(f"      {c}")
        if aplicar and texto != original:
            html.write_text(texto, encoding="utf-8")

    if desactualizados and not aplicar:
        print("\ncorregilo con: uv run python perfil/sincronizar.py --aplicar")
        return desactualizados
    if aplicar:
        print()
        imprimir()
    return desactualizados


def imprimir() -> bool:
    """Genera los PDF con Chrome headless y los copia al portfolio.

    El mismo Chrome con el que se imprimían a mano, así que el resultado es el
    de siempre. `--no-pdf-header-footer` saca la fecha y la URL que Chrome
    agrega por defecto en los márgenes — eso sí se vería distinto.
    """
    if not CHROME.is_file():
        print("no encuentro Chrome: los PDF hay que imprimirlos a mano")
        return False

    ok = True
    for html in HTMLS:
        nombres = SALIDAS.get(html.name)
        if not html.is_file() or nombres is None:
            continue
        destino = CV_DIR / nombres[0]
        resultado = subprocess.run(  # noqa: S603 - rutas fijas de este archivo
            [
                str(CHROME),
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={destino}",
                f"file://{html}",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if not destino.is_file() or destino.stat().st_size < 10_000:
            # Un PDF de menos de 10 KB no tiene la foto ni el contenido: algo
            # falló aunque Chrome no lo haya dicho por su código de salida.
            print(f"  ✗ {destino.name} no se generó bien: {resultado.stderr[-200:]}")
            ok = False
            continue
        print(f"  ✓ {destino.name} ({destino.stat().st_size:,} bytes)")

        # La copia del portfolio, que es la que ve quien entra a la web.
        publico = PORTFOLIO / "public" if PORTFOLIO else None
        if publico and publico.is_dir():
            shutil.copy2(destino, publico / nombres[1])
            print(f"      → portfolio/public/{nombres[1]}")

        # Y la de Drive, que es la que se manda desde el teléfono. Va con el
        # mismo nombre que ya tiene ahí para no dejar dos versiones conviviendo.
        if DRIVE.is_dir():
            shutil.copy2(destino, DRIVE / destino.name)
            print(f"      → Drive/{destino.name}")

    if ok and PORTFOLIO and (PORTFOLIO / ".git").is_dir():
        print("\n  El portfolio quedó con los PDF nuevos sin commitear.")
        print(f"  Revisalos y subilos:  cd {PORTFOLIO} && git add public && git commit")
    return ok


if __name__ == "__main__":
    revisar(aplicar="--aplicar" in sys.argv)
