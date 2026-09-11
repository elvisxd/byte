"""CLI de Byte: `byte ask`, `byte search`, `byte run`, `byte docs`, `byte status`.

Es el cliente en Python. El de Go (Fase 6) va a consumir la misma API con un
cliente generado desde el OpenAPI; el mapeo de comandos a endpoints está en
docs/api-contrato-byte.md y es el mismo que usa este.

Habla HTTP y nada más: no importa nada de `api/` ni de `agent/`, así que sirve
igual contra una instancia remota que contra la local.
"""

import argparse
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

# Paleta de la identidad, en ANSI (docs/prompts-canva-byte.md): prompt ámbar,
# respuestas en crema, estado en gris.
AMBAR = "\033[38;5;214m"
AZUL = "\033[38;5;61m"
CREMA = "\033[38;5;223m"
GRIS = "\033[38;5;245m"
ROJO = "\033[38;5;203m"
VERDE = "\033[38;5;71m"
NEGRITA = "\033[1m"
FIN = "\033[0m"

TIMEOUT_LARGO = 900.0  # un run con un modelo local en CPU puede tardar minutos


def _color(texto: str, codigo: str) -> str:
    """Sin color si la salida no es una terminal: `byte ask ... > archivo` no
    debería llenarse de códigos de escape."""
    return f"{codigo}{texto}{FIN}" if sys.stdout.isatty() else texto


def _error(mensaje: str) -> int:
    print(_color(f"✗ {mensaje}", ROJO), file=sys.stderr)
    return 1


class Pensando:
    """Spinner mientras el modelo responde, con el tiempo que lleva.

    Va en un hilo porque la petición HTTP bloquea el principal, y sobre stderr
    para no ensuciar la salida que alguien pueda estar redirigiendo a un
    archivo. Sin terminal no dibuja nada.
    """

    CUADROS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, texto: str = "pensando") -> None:
        self._texto = texto
        self._activo = False
        self._hilo: threading.Thread | None = None
        self._encendido = sys.stderr.isatty()

    def __enter__(self) -> "Pensando":
        if not self._encendido:
            return self
        self._activo = True
        sys.stderr.write("\033[?25l")  # esconde el cursor mientras gira
        self._hilo = threading.Thread(target=self._girar, daemon=True)
        self._hilo.start()
        return self

    def __exit__(self, *_: object) -> None:
        if not self._encendido:
            return
        self._activo = False
        if self._hilo:
            self._hilo.join(timeout=0.5)
        # Borra la línea del spinner y devuelve el cursor.
        sys.stderr.write("\r\033[2K\033[?25h")
        sys.stderr.flush()

    def _girar(self) -> None:
        arranque = time.monotonic()
        i = 0
        while self._activo:
            cuadro = self.CUADROS[i % len(self.CUADROS)]
            segundos = time.monotonic() - arranque
            sys.stderr.write(
                f"\r\033[2K{AMBAR}{cuadro}{FIN} {GRIS}{self._texto}… {segundos:.0f}s{FIN}"
            )
            sys.stderr.flush()
            time.sleep(0.08)
            i += 1


def _mensaje_de_error(respuesta: httpx.Response) -> str:
    """El mensaje del envelope de error de la API, o el texto crudo si no lo hay
    (un 502 de un proxy, por ejemplo, no devuelve JSON)."""
    if respuesta.headers.get("content-type", "").startswith("application/json"):
        cuerpo = respuesta.json()
        if isinstance(cuerpo, dict) and isinstance(cuerpo.get("error"), dict):
            return str(cuerpo["error"].get("message") or respuesta.text)
    return respuesta.text or f"HTTP {respuesta.status_code}"


class Byte:
    """Cliente HTTP de la API."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._cliente = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1",
            headers={"X-API-Key": api_key},
            timeout=TIMEOUT_LARGO,
        )

    def __enter__(self) -> "Byte":
        return self

    def __exit__(self, *_: object) -> None:
        self._cliente.close()

    def pedir(self, metodo: str, ruta: str, **kwargs: Any) -> Any:
        respuesta = self._cliente.request(metodo, ruta, **kwargs)
        if respuesta.status_code >= 400:
            raise RuntimeError(_mensaje_de_error(respuesta))
        return None if respuesta.status_code == 204 else respuesta.json()


def _config() -> tuple[str, str]:
    """URL y API key, del entorno o del .env del proyecto."""
    url = os.environ.get("BYTE_URL", "http://localhost:8000")
    clave = os.environ.get("BYTE_API_KEY", "")
    if not clave:
        # Conveniencia para desarrollo: si se corre desde el repo, el .env ya
        # tiene la clave y pedirla de nuevo sería ruido.
        env = Path(__file__).resolve().parent.parent / ".env"
        if env.is_file():
            for linea in env.read_text(encoding="utf-8").splitlines():
                if linea.startswith("BYTE_API_KEY="):
                    clave = linea.split("=", 1)[1].strip()
                    break
    return url, clave


# --- Bienvenida ---

# La mascota en bloques: orejas, cabeza con los ojos en negativo, y el collar
# con la etiqueta ámbar. Mismo espíritu que el logo SVG de la web.
MASCOTA = ["▐▛███▜▌", "▝▜█████▛▘", "  ▘▘ ▝▝  "]


def _bienvenida(byte: Byte, url: str) -> int:
    """Lo que se ve al escribir `byte` sin comando."""
    ancho = min(shutil.get_terminal_size((88, 24)).columns, 88)
    linea = "─" * (ancho - 2)

    def fila(texto: str = "", color: str = "") -> None:
        # El padding se calcula sobre el texto sin códigos ANSI, que no ocupan
        # ancho en pantalla pero sí caracteres en el string.
        relleno = " " * max(0, ancho - 4 - len(texto))
        print(f"│ {_color(texto, color) if color else texto}{relleno} │")

    print(_color(f"╭{linea}╮", AZUL))
    fila()
    for i, parte in enumerate(MASCOTA):
        centrada = parte.center(ancho - 4)
        fila(centrada, AMBAR if i == 2 else AZUL)
    fila()
    fila("  Byte — tu agente de IA, corriendo local", NEGRITA)
    fila()

    try:
        salud = byte.pedir("GET", "/health/details")
        herramientas = byte.pedir("GET", "/tools")["tools"]
        punto = "●" if salud["status"] == "ok" else "◐"
        fila(f"  {punto} {salud['model']} · {url}", VERDE if salud["status"] == "ok" else AMBAR)
        fila(f"    {', '.join(t['name'] for t in herramientas)}", GRIS)
    except Exception:  # noqa: BLE001 - la bienvenida no puede fallar por esto
        fila("  ○ sin conexión con la API", GRIS)
        fila(f"    {url}", GRIS)

    fila()
    fila('  byte ask "..."        preguntarle algo', GRIS)
    fila("  byte docs add x.pdf   sumar un documento", GRIS)
    fila("  byte --help           todos los comandos", GRIS)
    fila()
    print(_color(f"╰{linea}╯", AZUL))
    return 0


# --- Comandos ---


def cmd_ask(byte: Byte, args: argparse.Namespace) -> int:
    """Una pregunta al agente, esperando la respuesta completa.

    Usa `?wait=true` en vez del SSE: para un comando de una sola vuelta, seguir
    el stream solo agrega complejidad. El de Go, que muestra el texto token a
    token, sí va a usar los eventos.
    """
    conversacion = args.conversation or byte.pedir("POST", "/conversations", json={})["id"]
    with Pensando():
        datos = byte.pedir(
            "POST",
            f"/conversations/{conversacion}/messages",
            params={"wait": "true"},
            json={"content": args.pregunta, "safe_mode": args.safe},
        )

    if datos.get("status") == "paused":
        # Modo seguro: el run espera una decisión humana. Se muestra el código
        # y se resuelve con `byte approve` / `byte reject`.
        pendiente = datos["awaiting_approval"]
        print(_color("⚠ Byte quiere ejecutar código y espera tu confirmación", AMBAR))
        for codigo in pendiente.get("codes") or [pendiente.get("code", "")]:
            print(_color(codigo, GRIS))
        print()
        print(f"  byte approve {datos['run_id']} {pendiente['resume_token']}")
        print(f"  byte reject  {datos['run_id']} {pendiente['resume_token']}")
        return 2

    mensaje = datos["message"]
    print(mensaje["content"])
    fuentes = mensaje["metadata"].get("sources") or []
    if fuentes and sys.stdout.isatty():
        nombres = {f.get("filename") or f.get("url", "") for f in fuentes}
        print(_color("\nFuentes: " + " · ".join(sorted(n for n in nombres if n)), GRIS))
    if args.conversation is None and sys.stdout.isatty():
        print(_color(f"\nConversación: {conversacion}", GRIS))
    return 0


def cmd_resume(byte: Byte, args: argparse.Namespace) -> int:
    """Aprueba o rechaza una ejecución pendiente."""
    byte.pedir(
        "POST",
        f"/runs/{args.run_id}/resume",
        json={"resume_token": args.token, "approve": args.aprobar},
    )
    print(_color("✓ " + ("aprobado" if args.aprobar else "rechazado"), VERDE))
    print(_color("El run sigue: mirá la conversación con `byte ask -c <id>`", GRIS))
    return 0


def cmd_search(byte: Byte, args: argparse.Namespace) -> int:
    """Búsqueda híbrida en los documentos, sin pasar por el agente."""
    datos = byte.pedir("POST", "/search", json={"query": args.consulta, "top_k": args.top_k})
    if not datos["results"]:
        print(_color("Sin resultados en tus documentos.", GRIS))
        return 0
    for i, r in enumerate(datos["results"], start=1):
        print(f"{_color(f'[{i}] {r["filename"]}', AMBAR)}  {_color(f'{r["score"]:.3f}', GRIS)}")
        print(f"    {r['snippet']}")
    return 0


def cmd_run(byte: Byte, args: argparse.Namespace) -> int:
    """Ejecuta un archivo de Python en el sandbox, sin pasar por el modelo."""
    ruta = Path(args.archivo)
    if not ruta.is_file():
        return _error(f"no existe {ruta}")
    datos = byte.pedir(
        "POST",
        "/execute",
        json={"code": ruta.read_text(encoding="utf-8"), "timeout_s": args.timeout},
    )
    if datos["stdout"]:
        print(datos["stdout"], end="")
    if datos["stderr"]:
        print(_color(datos["stderr"], ROJO), end="", file=sys.stderr)
    if datos["truncated"]:
        print(_color("[salida recortada]", GRIS), file=sys.stderr)
    # El exit_code del sandbox se propaga: así `byte run` encadena en un script.
    return int(datos["exit_code"])


def cmd_docs_add(byte: Byte, args: argparse.Namespace) -> int:
    ruta = Path(args.archivo)
    if not ruta.is_file():
        return _error(f"no existe {ruta}")
    with ruta.open("rb") as archivo:
        datos = byte.pedir("POST", "/documents", files={"file": (ruta.name, archivo)})
    print(_color(f"✓ {datos['filename']} subido", VERDE))
    print(_color("  indexando… seguilo con `byte docs list`", GRIS))
    return 0


def cmd_docs_list(byte: Byte, _args: argparse.Namespace) -> int:
    datos = byte.pedir("GET", "/documents")
    if not datos["items"]:
        print(_color("Todavía no subiste documentos.", GRIS))
        return 0
    colores = {"indexed": VERDE, "processing": AMBAR, "error": ROJO}
    for doc in datos["items"]:
        estado = _color(f"{doc['status']:<10}", colores.get(doc["status"], GRIS))
        fragmentos = f"{doc['chunks']} frag." if doc["status"] == "indexed" else ""
        print(f"{estado} {doc['filename']:<34} {_color(fragmentos, GRIS)}")
        if doc["error_message"]:
            print(f"           {_color(doc['error_message'], GRIS)}")
    return 0


def cmd_docs_rm(byte: Byte, args: argparse.Namespace) -> int:
    byte.pedir("DELETE", f"/documents/{args.id}")
    print(_color("✓ eliminado", VERDE))
    return 0


def cmd_status(byte: Byte, _args: argparse.Namespace) -> int:
    salud = byte.pedir("GET", "/health/details")
    ok = salud["status"] == "ok"
    print(
        _color("● " + ("en línea, corriendo local" if ok else "degradado"), VERDE if ok else AMBAR)
    )
    for clave in ("model", "ollama", "db", "sandbox"):
        print(f"  {clave:<9} {salud[clave]}")
    herramientas = byte.pedir("GET", "/tools")["tools"]
    print(f"  {'tools':<9} " + ", ".join(t["name"] for t in herramientas))
    return 0


def cmd_conversations(byte: Byte, args: argparse.Namespace) -> int:
    datos = byte.pedir("GET", "/conversations", params={"limit": args.limit})
    for conv in datos["items"]:
        print(f"{_color(conv['id'], GRIS)}  {conv['title']}")
    return 0


# --- Parseo ---


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="byte", description="Byte: tu agente de IA local, desde la terminal."
    )
    # Sin `required`: `byte` a secas muestra la bienvenida, como Claude Code.
    sub = parser.add_subparsers(dest="comando")

    p = sub.add_parser("ask", help="preguntarle algo al agente")
    p.add_argument("pregunta")
    p.add_argument("-c", "--conversation", help="seguir una conversación existente")
    p.add_argument(
        "--safe", action="store_true", help="pedir confirmación antes de ejecutar código"
    )
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("approve", help="aprobar una ejecución pendiente")
    p.add_argument("run_id")
    p.add_argument("token")
    p.set_defaults(func=cmd_resume, aprobar=True)

    p = sub.add_parser("reject", help="rechazar una ejecución pendiente")
    p.add_argument("run_id")
    p.add_argument("token")
    p.set_defaults(func=cmd_resume, aprobar=False)

    p = sub.add_parser("search", help="buscar en tus documentos")
    p.add_argument("consulta")
    p.add_argument("-k", "--top-k", type=int, default=5)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("run", help="ejecutar un archivo Python en el sandbox")
    p.add_argument("archivo")
    p.add_argument("--timeout", type=int, default=10, help="segundos (1-30)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("docs", help="tus documentos")
    docs = p.add_subparsers(dest="accion", required=True)
    a = docs.add_parser("add", help="subir un documento")
    a.add_argument("archivo")
    a.set_defaults(func=cmd_docs_add)
    a = docs.add_parser("list", help="listar los documentos")
    a.set_defaults(func=cmd_docs_list)
    a = docs.add_parser("rm", help="eliminar un documento")
    a.add_argument("id")
    a.set_defaults(func=cmd_docs_rm)

    p = sub.add_parser("status", help="estado de Byte y sus servicios")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("conversations", help="listar conversaciones")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.set_defaults(func=cmd_conversations)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    url, clave = _config()
    if not clave:
        return _error("falta BYTE_API_KEY (o un .env con ella)")

    try:
        with Byte(url, clave) as byte:
            if args.comando is None:
                return _bienvenida(byte, url)
            return int(args.func(byte, args))
    except httpx.ConnectError:
        return _error(f"no responde {url} — ¿está levantada la API?")
    except httpx.TimeoutException:
        return _error("la API tardó demasiado")
    except RuntimeError as exc:
        return _error(str(exc))
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
