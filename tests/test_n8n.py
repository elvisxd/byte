"""Los workflows de n8n que se versionan en `n8n/`.

No se levanta n8n acá: eso se verifica a mano y está anotado en `n8n/README.md`.
Lo que se prueba es lo que un JSON mal escrito rompe en silencio —una conexión
que apunta a un nodo que no existe, o una condición de seguridad que no filtra
lo que dice filtrar— y que se descubriría recién en producción.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / "n8n").glob("*.json"))

NODE = shutil.which("node")
necesita_node = pytest.mark.skipif(NODE is None, reason="hace falta node para evaluar la expresión")


def _cargar(nombre: str) -> dict:
    return json.loads((Path(__file__).resolve().parent.parent / "n8n" / nombre).read_text("utf-8"))


@pytest.mark.parametrize("ruta", WORKFLOWS, ids=lambda p: p.name)
def test_las_conexiones_apuntan_a_nodos_que_existen(ruta: Path) -> None:
    """n8n descarta en silencio una conexión hacia un nodo inexistente: el
    workflow importa igual y queda partido a la mitad."""
    wf = json.loads(ruta.read_text("utf-8"))
    nombres = {n["name"] for n in wf["nodes"]}
    for origen, salidas in wf["connections"].items():
        assert origen in nombres, f"{ruta.name}: la conexión sale de '{origen}', que no existe"
        for ramas in salidas.values():
            for rama in ramas:
                for destino in rama:
                    assert destino["node"] in nombres, (
                        f"{ruta.name}: '{origen}' apunta a '{destino['node']}', que no existe"
                    )


@pytest.mark.parametrize("ruta", WORKFLOWS, ids=lambda p: p.name)
def test_ningun_workflow_lleva_credenciales(ruta: Path) -> None:
    """Se versionan: una credencial acá queda en el repo para siempre.

    Cada nodo que necesita una lo dice en sus notas y se configura en n8n.
    """
    crudo = ruta.read_text("utf-8")
    wf = json.loads(crudo)
    for nodo in wf["nodes"]:
        assert "credentials" not in nodo, f"{ruta.name}: '{nodo['name']}' lleva una credencial"
    # Un token o una clave pegada en un parámetro no la ve el chequeo de arriba.
    for sospechoso in ("Bearer ey", "sk-", "api_key=", "password"):
        assert sospechoso not in crudo, f"{ruta.name}: parece tener un secreto ({sospechoso})"


@pytest.mark.parametrize("ruta", WORKFLOWS, ids=lambda p: p.name)
def test_los_workflows_quedan_desactivados(ruta: Path) -> None:
    """Uno activo a medio configurar falla en cada ejecución: se activan después
    de cargarle las credenciales."""
    assert json.loads(ruta.read_text("utf-8"))["active"] is False


# --- El filtro de remitente, que es la seguridad del canal de email ---


def _evaluar(guion: str) -> str:
    """Corre un guion armado en este archivo, con la ruta absoluta de node.

    No hay entrada de un tercero: lo único variable es el remitente de prueba,
    que se inserta con `json.dumps`.
    """
    assert NODE is not None  # noqa: S101 - lo garantiza `necesita_node`
    return subprocess.run(  # noqa: S603 - guion propio, binario resuelto
        [NODE, "-e", guion], capture_output=True, text=True, check=True
    ).stdout.strip()


def _condicion_del_filtro() -> str:
    wf = _cargar("canal-email.json")
    nodo = next(n for n in wf["nodes"] if n["name"] == "¿Remitente permitido?")
    expresion = nodo["parameters"]["conditions"]["conditions"][0]["leftValue"]
    return expresion.removeprefix("={{").removesuffix("}}").strip()


@necesita_node
@pytest.mark.parametrize(
    ("remitente", "permitido"),
    [
        ('"Elvis" <elvis@ejemplo.com>', True),
        ("elvis@ejemplo.com", True),
        ("ELVIS@EJEMPLO.COM", True),
        ("otro@ejemplo.com", True),
        ("atacante@evil.com", False),
        # El nombre para mostrar lo escribe quien manda el correo: con un
        # `contains` sobre el From entero, esto entraba.
        ('"elvis@ejemplo.com" <atacante@evil.com>', False),
        ("elvis@ejemplo.com.evil.com", False),
        ("", False),
    ],
)
def test_el_filtro_de_remitente_decide_bien(remitente: str, permitido: bool) -> None:
    """La condición que decide quién puede usar tu modelo.

    Se evalúa la expresión real del JSON con node, no una copia: si alguien la
    edita y la rompe, esto tiene que fallar.
    """
    guion = f"""
const $env = {{ BYTE_EMAIL_PERMITIDOS: "elvis@ejemplo.com, otro@ejemplo.com" }};
const $json = {{ from: {json.dumps(remitente)} }};
process.stdout.write(String({_condicion_del_filtro()}));
"""
    salida = _evaluar(guion)
    assert salida == str(permitido).lower(), f"'{remitente}' → {salida}, se esperaba {permitido}"


@necesita_node
def test_sin_lista_configurada_no_entra_nadie() -> None:
    """Si `BYTE_EMAIL_PERMITIDOS` quedó sin definir, lo seguro es no responderle
    a nadie —no responderle a todos."""
    guion = f"""
const $env = {{}};
const $json = {{ from: "cualquiera@internet.com" }};
process.stdout.write(String({_condicion_del_filtro()}));
"""
    assert _evaluar(guion) == "false"
