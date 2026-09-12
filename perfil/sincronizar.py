"""Mantiene al día los números del CV en los HTML de los que salen los PDF.

El flujo real es: `~/Downloads/cv-elvis/build/cv-{en,es}.html` se abren en el
navegador y se imprimen a PDF, y esos PDF van al portfolio y a Drive. Los HTML
están escritos a mano, así que un dato como "183 tests" hay que cambiarlo en
dos archivos y reimprimir dos PDF — y por eso quedó viejo: hoy son 459.

Esto no reescribe el CV, que es tuyo. Solo busca los números que envejecen y
los actualiza, diciendo exactamente qué cambió. Lo demás sigue en tus manos.

    uv run python perfil/sincronizar.py           # dice qué está desactualizado
    uv run python perfil/sincronizar.py --aplicar # lo corrige
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from perfil.actualizar import todo  # noqa: E402

CV_DIR = Path.home() / "Downloads" / "cv-elvis"
HTMLS = [CV_DIR / "build" / "cv-en.html", CV_DIR / "build" / "cv-es.html"]


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
    elif desactualizados:
        print("\nListo. Falta reimprimir los PDF:")
        print(f"  abrí {HTMLS[0]} en el navegador → Imprimir → Guardar como PDF")
        print("  y lo mismo con el -es. Después subilos al portfolio y a Drive.")
    return desactualizados


if __name__ == "__main__":
    revisar(aplicar="--aplicar" in sys.argv)
