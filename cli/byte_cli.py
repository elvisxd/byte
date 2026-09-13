"""CLI de Byte: `byte ask`, `byte search`, `byte run`, `byte docs`, `byte status`.

Es el cliente en Python. El de Go (Fase 6) va a consumir la misma API con un
cliente generado desde el OpenAPI; el mapeo de comandos a endpoints está en
docs/api-contrato-byte.md y es el mismo que usa este.

Habla HTTP y nada más: no importa nada de `api/` ni de `agent/`, así que sirve
igual contra una instancia remota que contra la local.
"""

import argparse
import getpass
import json
import os
import re
import shutil
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from cli import sesion

try:  # readline le da historial y edición de línea a `input()`; en Windows no está.
    import readline  # noqa: F401
except ImportError:  # pragma: no cover - depende de la plataforma
    pass

# Paleta de la identidad, en ANSI (docs/prompts-canva-byte.md): prompt ámbar,
# respuestas en crema, estado en gris.
AMBAR = "\033[38;5;214m"
AZUL = "\033[38;5;61m"
CREMA = "\033[38;5;223m"
GRIS = "\033[38;5;245m"
ROJO = "\033[38;5;203m"
VERDE = "\033[38;5;71m"
NEGRITA = "\033[1m"
# Fondo del bloque de la pregunta. Un gris apenas más claro que el negro de la
# terminal: alcanza para que el ojo lo separe de la respuesta sin competir con
# ella. 237 se ve igual en fondo oscuro y en uno claro razonable.
FONDO = "\033[48;5;237m"
FIN = "\033[0m"

TIMEOUT_LARGO = 900.0  # un run con un modelo local en CPU puede tardar minutos


def _color(texto: str, codigo: str) -> str:
    """Sin color si la salida no es una terminal: `byte ask ... > archivo` no
    debería llenarse de códigos de escape."""
    return f"{codigo}{texto}{FIN}" if sys.stdout.isatty() else texto


def _en_pantalla() -> bool:
    """Si alguien está mirando esto en una terminal, y no un archivo o un pipe."""
    return sys.stdout.isatty()


def _recortar(texto: str, ancho: int) -> str:
    """Una línea larga en el estado empujaría el spinner fuera de la pantalla."""
    texto = " ".join(texto.split())  # los saltos de línea romperían la línea viva
    return texto if len(texto) <= ancho else texto[: max(0, ancho - 1)] + "…"


def _interactiva() -> bool:
    """Si hay una terminal de verdad de los dos lados.

    Es lo que decide si `byte` a secas abre el chat o solo se presenta: un REPL
    leyendo de un pipe o escribiendo a un archivo no le sirve a nadie.
    """
    return sys.stdin.isatty() and sys.stdout.isatty()


def _error(mensaje: str) -> int:
    print(_color(f"✗ {mensaje}", ROJO), file=sys.stderr)
    return 1


class Estado:
    """La línea viva de abajo: qué está haciendo el agente, ahora mismo.

    El verbo lo cambia el stream de eventos (`Working`, `Searching the web`,
    `Running code`…), así que no es un texto fijo: dice lo que de verdad está
    pasando. Gira en un hilo porque el stream bloquea el principal, y escribe en
    stderr para no ensuciar una salida redirigida. Sin terminal no dibuja nada.
    """

    CUADROS = "✢✳✻✽✻✳"

    # Palabras que rotan mientras el modelo piensa, en vez de un "Working" fijo.
    # Un modelo local tarda decenas de segundos y la línea quieta parece colgada;
    # que cambie cada pocos segundos dice "sigo acá" sin prometer progreso que no
    # se puede medir. Solo se usan cuando el verbo es genérico: si el stream dijo
    # "Searching the web", eso es información de verdad y no se pisa.
    OCURRENCIAS = (
        "Reticulating",
        "Pondering",
        "Percolating",
        "Noodling",
        "Simmering",
        "Cogitating",
        "Ruminating",
        "Marinating",
    )
    # Cada cuántos segundos cambia la palabra.
    CADA = 4.0

    def __init__(self, texto: str = "Working") -> None:
        self._texto = texto
        self._detalle = ""
        self._activo = False
        self._hilo: threading.Thread | None = None
        self._encendido = sys.stderr.isatty()
        self._arranque = time.monotonic()
        self._candado = threading.Lock()

    def __enter__(self) -> "Estado":
        if not self._encendido:
            return self
        self._activo = True
        self._arranque = time.monotonic()
        sys.stderr.write("\033[?25l")  # esconde el cursor mientras gira
        self._hilo = threading.Thread(target=self._girar, daemon=True)
        self._hilo.start()
        return self

    def __exit__(self, *_: object) -> None:
        if not self._encendido:
            return
        if self._activo:
            self.detener()
        else:
            # Ya lo paró el primer token de la respuesta; queda asegurar el cursor.
            sys.stderr.write("\033[?25h")
            sys.stderr.flush()

    def seguir(self, texto: str) -> None:
        """Vuelve a girar después de un `detener()`.

        Hace falta porque entre una herramienta y la siguiente se imprime una
        línea de trabajo: el spinner para, se escribe, y arranca de nuevo.
        """
        if not self._encendido or self._activo:
            return
        self.cambiar(texto)
        self._activo = True
        sys.stderr.write("\033[?25l")
        self._hilo = threading.Thread(target=self._girar, daemon=True)
        self._hilo.start()

    def cambiar(self, texto: str, detalle: str = "") -> None:
        """El verbo nuevo; `detalle` es el argumento (la consulta, el archivo)."""
        with self._candado:
            self._texto, self._detalle = texto, detalle

    def detener(self) -> None:
        """Para el spinner y borra su línea, para no pisar lo que venga después.

        Lo llama quien empieza a escribir la respuesta: si solo se borrara la
        línea, el hilo la volvería a dibujar en el siguiente cuadro y el texto
        saldría entrelazado con el spinner.
        """
        if not self._encendido or not self._activo:
            return
        self._activo = False
        if self._hilo:
            self._hilo.join(timeout=0.5)
        sys.stderr.write("\r\033[2K\033[?25h")
        sys.stderr.flush()

    def _girar(self) -> None:
        i = 0
        while self._activo:
            with self._candado:
                texto, detalle = self._texto, self._detalle
            cuadro = self.CUADROS[i % len(self.CUADROS)]
            segundos = time.monotonic() - self._arranque

            # El verbo genérico se reemplaza por una palabra que rota; uno
            # específico ("Searching the web") se respeta, porque dice algo que
            # la palabra inventada no sabe.
            # Con el verbo genérico la palabra rota y el paréntesis aporta el
            # "thinking"; con uno específico ("Searching the web") ese mismo
            # texto ya está a la izquierda, y repetirlo adentro solo gasta
            # ancho de línea.
            if texto == "Working":
                palabra = self.OCURRENCIAS[int(segundos / self.CADA) % len(self.OCURRENCIAS)]
                dentro = f"{segundos:.0f}s · thinking"
            else:
                palabra = texto
                dentro = f"{segundos:.0f}s"

            # `detalle` —la consulta, el archivo— va adentro cuando lo hay.
            if detalle:
                dentro += f" · {detalle}"
            sys.stderr.write(
                f"\r\033[2K{AMBAR}{cuadro}{FIN} {CREMA}{palabra}…{FIN} {GRIS}({dentro}){FIN}"
            )
            sys.stderr.flush()
            time.sleep(0.12)
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
        self._url = base_url.rstrip("/")
        self._api_key = api_key
        # Si hay sesión iniciada se usa el token del usuario; si no, la API key,
        # que es la credencial de la instancia. Las dos van en el mismo header
        # para la API, pero significan cosas distintas: con token las
        # conversaciones son tuyas, con la clave son de la instancia.
        guardada = sesion.leer(self._url)
        self._sesion = guardada
        self._cliente = httpx.Client(
            base_url=self._url + "/api/v1",
            headers=self._cabeceras(),
            timeout=TIMEOUT_LARGO,
        )

    def _cabeceras(self) -> dict[str, str]:
        if self._sesion and self._sesion.get("access_token"):
            return {"Authorization": f"Bearer {self._sesion['access_token']}"}
        return {"X-API-Key": self._api_key}

    @property
    def email(self) -> str | None:
        """Con quién se está trabajando, o `None` si se usa la API key."""
        return self._sesion.get("email") if self._sesion else None

    def _renovar(self) -> bool:
        """Cambia el access token vencido por uno nuevo, sin pedir contraseña.

        El access dura 30 minutos y el refresh 14 días: sin esto habría que
        volver a entrar cada media hora, que es justo lo que el refresh evita.
        El refresh se rota en cada uso, así que el nuevo se guarda enseguida.

        **Se relee el archivo antes de renovar**, y si otra terminal ya renovó
        se usa lo que dejó en vez de intentar de nuevo. Dos terminales abiertas
        es el caso normal: las dos cargan el mismo refresh al arrancar, y sin
        esto la segunda presentaba uno ya gastado — el servidor lo lee como
        reuso, revoca la familia entera, y quien no hizo nada mal tiene que
        volver a entrar.
        """
        if not self._sesion or not self._sesion.get("refresh_token"):
            return False

        en_disco = sesion.leer(self._url)
        if en_disco and en_disco.get("access_token") != self._sesion.get("access_token"):
            # Otra terminal renovó mientras tanto: se toma lo suyo y listo.
            self._sesion = en_disco
            self._cliente.headers.update(self._cabeceras())
            return True

        try:
            respuesta = self._cliente.post(
                "/auth/refresh",
                json={"refresh_token": self._sesion["refresh_token"]},
                headers={},  # sin el access vencido, que la API rechazaría
            )
        except httpx.HTTPError:
            return False
        if respuesta.status_code >= 400:
            # El refresh tampoco vale: o venció, o alguien lo reusó y la familia
            # se revocó. En los dos casos hay que volver a entrar.
            sesion.borrar(self._url)
            self._sesion = None
            self._cliente.headers.update(self._cabeceras())
            return False

        datos = respuesta.json()
        # Una respuesta 200 sin los campos esperados no debería salir como un
        # traceback pelado: es un servidor que cambió, no un bug del usuario.
        if not isinstance(datos, dict) or not datos.get("access_token"):
            return False
        sesion.guardar(
            self._url,
            datos["access_token"],
            datos.get("refresh_token", ""),
            self._sesion.get("email", ""),
        )
        self._sesion = sesion.leer(self._url)
        self._cliente.headers.update(self._cabeceras())
        return True

    def __enter__(self) -> "Byte":
        return self

    def __exit__(self, *_: object) -> None:
        self._cliente.close()

    def pedir(self, metodo: str, ruta: str, **kwargs: Any) -> Any:
        respuesta = self._cliente.request(metodo, ruta, **kwargs)
        # Un 401 con sesión iniciada casi siempre es el access vencido: se
        # renueva y se reintenta una sola vez. Reintentar en bucle convertiría
        # una credencial mala en una tormenta de pedidos.
        if respuesta.status_code == 401 and self._sesion and self._renovar():
            respuesta = self._cliente.request(metodo, ruta, **kwargs)
        if respuesta.status_code >= 400:
            raise RuntimeError(_mensaje_de_error(respuesta))
        return None if respuesta.status_code == 204 else respuesta.json()

    def _abrir_stream(self, ruta: str) -> httpx.Response:
        """Abre el stream sin leerlo, para poder mirar el código antes."""
        peticion = self._cliente.build_request("GET", ruta, timeout=TIMEOUT_LARGO)
        return self._cliente.send(peticion, stream=True)

    def eventos(self, ruta: str) -> Iterator[tuple[str, dict[str, Any]]]:
        """Los eventos AG-UI de un run, a medida que llegan.

        Un SSE trae bloques separados por una línea en blanco; de cada uno solo
        interesan `event:` y `data:`. Los comentarios de keepalive (`:`) se
        ignoran solos porque no tienen ninguno de los dos campos.

        Un 401 acá también renueva y reintenta, igual que en `pedir`. La ventana
        es angosta —el POST que arranca el run ya renovó hace milisegundos— pero
        si el access vence justo entre los dos, el chat falla con "credencial
        inválida" en medio de una respuesta, que es de lo más confuso que puede
        pasarle a alguien que está escribiendo.
        """
        respuesta = self._abrir_stream(ruta)
        if respuesta.status_code == 401 and self._sesion and self._renovar():
            respuesta.close()
            respuesta = self._abrir_stream(ruta)
        try:
            if respuesta.status_code >= 400:
                respuesta.read()
                raise RuntimeError(_mensaje_de_error(respuesta))
            tipo, datos = "", ""
            for linea in respuesta.iter_lines():
                if linea.startswith("event:"):
                    tipo = linea[6:].strip()
                elif linea.startswith("data:"):
                    datos = linea[5:].strip()
                elif not linea.strip() and tipo:
                    try:
                        yield tipo, json.loads(datos) if datos else {}
                    except json.JSONDecodeError:
                        pass  # un evento ilegible no debería cortar el stream
                    tipo, datos = "", ""
        finally:
            # Sin esto la conexión queda abierta si quien consume el generador
            # corta antes de tiempo — por ejemplo con Ctrl-C en medio de una
            # respuesta, que es justo cuando más pasa.
            respuesta.close()


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

ANCHO_MAXIMO = 104
ANCHO_MINIMO_DOS_COLUMNAS = 90  # por debajo de esto la derecha queda ilegible

# `GET /tools` devuelve solo nombre y origen; para qué sirve cada una es
# decisión de presentación y vive acá, no en la API.
QUE_HACE = {
    "doc_search": "searches the documents you added",
    "web_search": "looks things up on the web",
    "code_exec": "runs Python in a sandbox",
}


def _visible(texto: str) -> int:
    """El ancho en pantalla: los códigos ANSI ocupan caracteres pero no columnas."""
    return len(re.sub(r"\033\[[0-9;]*m", "", texto))


def _rellenar(texto: str, ancho: int) -> str:
    """Lleva la celda a `ancho` columnas, recortando con elipsis si se pasa."""
    sobra = ancho - _visible(texto)
    if sobra >= 0:
        return texto + " " * sobra
    # Recorta contando solo lo visible, para no cortar un código ANSI al medio.
    salida, usado = "", 0
    for trozo in re.split(r"(\033\[[0-9;]*m)", texto):
        if trozo.startswith("\033"):
            salida += trozo
            continue
        for caracter in trozo:
            if usado >= ancho - 1:
                return salida + "…" + FIN
            salida += caracter
            usado += 1
    return salida + " " * (ancho - usado)


def _centrar(texto: str, ancho: int) -> str:
    izquierda = max(0, (ancho - _visible(texto)) // 2)
    return _rellenar(" " * izquierda + texto, ancho)


def _panel_identidad(byte: Byte, url: str, ancho: int) -> list[str]:
    """Columna izquierda: quién es y contra qué está corriendo."""
    filas = [""]
    filas += [_centrar(_color(p, AMBAR if i == 2 else AZUL), ancho) for i, p in enumerate(MASCOTA)]
    filas.append("")
    filas.append(_centrar(_color("Byte", NEGRITA) + _color(" — your AI agent", GRIS), ancho))
    filas.append(_centrar(_color("running local", GRIS), ancho))
    filas.append("")
    try:
        salud = byte.pedir("GET", "/health/details")
        ok = salud["status"] == "ok"
        punto = _color("●" if ok else "◐", VERDE if ok else AMBAR)
        filas.append(_centrar(f"{punto} {_color(salud['model'], CREMA)}", ancho))
    except Exception:  # noqa: BLE001 - la bienvenida no puede fallar por esto
        filas.append(_centrar(_color("○ no connection", ROJO), ancho))
    filas.append(_centrar(_color(url, GRIS), ancho))
    # Con quién se está trabajando. Sin sesión no se dice nada: la API key es
    # el caso normal y ponerle una etiqueta sería ruido.
    if byte.email:
        filas.append(_centrar(_color(byte.email, CREMA), ancho))
    return filas


def _panel_pistas(byte: Byte, interactivo: bool, ancho: int) -> list[str]:
    """Columna derecha: qué podés hacer y con qué cuenta."""
    filas = [_color("Tips for getting started", NEGRITA)]
    if interactivo:
        filas.append(_color("Just type — it answers and remembers the conversation", GRIS))
        filas.append(
            _color("Run ", GRIS)
            + _color("/add", CREMA)
            + _color(" or ", GRIS)
            + _color("/search", CREMA)
            + _color(" to work with your documents", GRIS)
        )
        filas.append(
            _color("Run ", GRIS) + _color("/help", CREMA) + _color(" to see the rest", GRIS)
        )
    else:
        filas.append(
            _color("Run ", GRIS)
            + _color('byte ask "..."', CREMA)
            + _color(" to ask it something", GRIS)
        )
        filas.append(
            _color("Run ", GRIS)
            + _color("byte docs add x.pdf", CREMA)
            + _color(" to add a document", GRIS)
        )
        filas.append(
            _color("Run ", GRIS) + _color("byte --help", CREMA) + _color(" for all commands", GRIS)
        )

    filas.append(_color("─" * ancho, GRIS))
    filas.append(_color("What it can use", NEGRITA))
    try:
        for t in byte.pedir("GET", "/tools")["tools"]:
            nombre = t["name"]
            filas.append(_color(f"{nombre:<12}", CREMA) + _color(QUE_HACE.get(nombre, ""), GRIS))
    except Exception:  # noqa: BLE001
        filas.append(_color("unavailable while the API is down", GRIS))
    return filas


def _bienvenida(byte: Byte, url: str, interactivo: bool = False) -> int:
    """Lo que se ve al escribir `byte` sin comando.

    En dos columnas, como las herramientas de agente modernas: a la izquierda
    quién sos y contra qué corrés, a la derecha qué podés hacer. En una terminal
    angosta se cae a una sola columna, que es lo único que entra.
    """
    ancho = min(shutil.get_terminal_size((ANCHO_MAXIMO, 24)).columns, ANCHO_MAXIMO)
    dos_columnas = ancho >= ANCHO_MINIMO_DOS_COLUMNAS

    # Interior = ancho - 2 bordes. Con dos columnas hay un separador y un
    # espacio de aire a cada lado de cada celda: 1 + izq + 1 + │ + 1 + der + 1.
    interior = ancho - 2
    ancho_izq = 27 if dos_columnas else interior - 2
    ancho_der = interior - ancho_izq - 5 if dos_columnas else 0

    izquierda = _panel_identidad(byte, url, ancho_izq)
    derecha = _panel_pistas(byte, interactivo, ancho_der) if dos_columnas else []

    borde = lambda c: _color(c, AZUL)  # noqa: E731
    print(borde("╭" + "─" * interior + "╮"))
    for i in range(max(len(izquierda), len(derecha))):
        celda_izq = izquierda[i] if i < len(izquierda) else ""
        linea = borde("│") + " " + _rellenar(celda_izq, ancho_izq) + " "
        if dos_columnas:
            celda_der = derecha[i] if i < len(derecha) else ""
            linea += borde("│") + " " + _rellenar(celda_der, ancho_der) + " "
        print(linea + borde("│"))
    print(borde("╰" + "─" * interior + "╯"))
    return 0


# --- Chat interactivo ---

# Los comandos del chat llevan `/` para no confundirse con lo que se le
# pregunta al modelo: "status" bien puede ser una pregunta, "/status" no.
AYUDA_CHAT = [
    ("/new", "start a fresh conversation, with no memory of this one"),
    ("/docs", "list your documents"),
    ("/add <file>", "add a document"),
    ("/search <text>", "search your documents, without asking the model"),
    ("/run <file.py>", "run a Python file in the sandbox"),
    ("/safe", "toggle asking before running code"),
    ("/model [name]", "list models, or switch to one"),
    ("/status", "Byte and its services"),
    ("/id", "this conversation's id"),
    ("/help", "this help"),
    ("/exit", "leave (or Ctrl-D)"),
]

# Cada comando acepta variantes: el banner está en inglés, pero quien escribe en
# español no debería chocarse con un "no conozco /salir".
ALIAS_CHAT = {
    "/salir": "/exit",
    "/quit": "/exit",
    "/q": "/exit",
    "/nueva": "/new",
    "/ayuda": "/help",
    "/h": "/help",
    "/?": "/help",
    "/buscar": "/search",
    "/agregar": "/add",
    "/seguro": "/safe",
    "/estado": "/status",
    "/modelo": "/model",
    "/m": "/model",
}


def _cambiar_de_modelo(byte: Byte, pedido: str, sesion: "Sesion") -> None:
    """`/model` lista; `/model <nombre>` cambia para lo que resta de la charla.

    Se acepta un prefijo ("granite" por "granite4.1:8b") porque los nombres
    llevan versión y tag, y escribirlos enteros para cambiar de modelo es una
    fricción que no aporta nada.

    **Lo que cuesta cambiar se dice antes de cambiar.** En una máquina donde no
    entran dos modelos en memoria, Ollama desaloja uno para cargar el otro y la
    primera respuesta tarda ~27 s más. Sin el aviso, parece que Byte se colgó.
    """
    salud = byte.pedir("GET", "/health/details")
    modelos = list(salud.get("models") or [])
    if not modelos:
        modelos = [str(salud.get("model", ""))]
    activo = sesion.modelo or modelos[0]

    if not pedido:
        if len(modelos) == 1:
            print(_color(f"{activo} — the only one configured", GRIS))
            print(_color("add more with OLLAMA_MODELS_DISPONIBLES in .env", GRIS))
            return
        for nombre in modelos:
            marca = _color(" ● active", VERDE) if nombre == activo else ""
            print(f"  {_color(nombre, CREMA)}{marca}")
        print(_color("switch with /model <name>", GRIS))
        return

    # Coincidencia exacta primero: si alguien escribe el nombre entero, eso gana
    # sobre cualquier prefijo que también encaje.
    elegido = next((n for n in modelos if n == pedido), "")
    if not elegido:
        candidatos = [n for n in modelos if n.startswith(pedido)]
        if len(candidatos) > 1:
            print(_color(f"'{pedido}' matches several: {', '.join(candidatos)}", GRIS))
            return
        elegido = candidatos[0] if candidatos else ""

    if not elegido:
        print(_color(f"I don't have '{pedido}'. Configured: {', '.join(modelos)}", GRIS))
        return

    if elegido == activo:
        print(_color(f"already using {elegido}", GRIS))
        return

    sesion.modelo = elegido
    print(_color(f"✓ now using {elegido}", VERDE))
    print(_color("the next answer will take ~30s longer while Ollama loads it", GRIS))


def _nueva_conversacion(byte: Byte) -> str:
    return str(byte.pedir("POST", "/conversations", json={})["id"])


class Sesion:
    """Lo que el chat recuerda entre turnos: la conversación y el modo seguro."""

    def __init__(self, conversacion: str, safe: bool) -> None:
        self.conversacion = conversacion
        self.safe = safe
        self.seguir = True
        # Modelo elegido con `/model`. Vacío = el default de la instancia.
        self.modelo = ""


def _comando_del_chat(byte: Byte, entrada: str, sesion: Sesion) -> None:
    """Atiende un `/comando`, cambiando la sesión si hace falta.

    Los errores de la API se muestran y no cortan la sesión: que `/docs` falle
    porque la API se cayó no es razón para perder el hilo de la charla.
    """
    partes = entrada.split(maxsplit=1)
    orden = ALIAS_CHAT.get(partes[0].lower(), partes[0].lower())
    resto = partes[1].strip() if len(partes) > 1 else ""

    if orden == "/exit":
        sesion.seguir = False
        return

    try:
        if orden == "/new":
            sesion.conversacion = _nueva_conversacion(byte)
            print(_color("✓ fresh conversation, no memory of the previous one", VERDE))

        elif orden == "/docs":
            cmd_docs_list(byte, argparse.Namespace())

        elif orden == "/add":
            if not resto:
                print(_color("which file? — /add report.pdf", GRIS))
            else:
                cmd_docs_add(byte, argparse.Namespace(archivo=os.path.expanduser(resto)))

        elif orden == "/search":
            if not resto:
                print(_color("search for what? — /search deployment", GRIS))
            else:
                cmd_search(byte, argparse.Namespace(consulta=resto, top_k=5))

        elif orden == "/run":
            if not resto:
                print(_color("which file? — /run script.py", GRIS))
            else:
                cmd_run(byte, argparse.Namespace(archivo=os.path.expanduser(resto), timeout=10))

        elif orden == "/safe":
            sesion.safe = not sesion.safe
            estado = "on — it will ask before running code" if sesion.safe else "off"
            print(_color(f"✓ safe mode {estado}", VERDE if sesion.safe else GRIS))

        elif orden == "/model":
            _cambiar_de_modelo(byte, resto, sesion)

        elif orden == "/status":
            cmd_status(byte, argparse.Namespace())

        elif orden == "/id":
            print(_color(sesion.conversacion, GRIS))
            print(_color(f'pick it up later with: byte ask -c {sesion.conversacion} "..."', GRIS))

        elif orden == "/help":
            for nombre, que_hace in AYUDA_CHAT:
                print(f"  {_color(f'{nombre:<16}', AMBAR)}{_color(que_hace, GRIS)}")

        else:
            print(_color(f"I don't know {orden} — try /help", GRIS))

    except (RuntimeError, httpx.HTTPError) as exc:
        _error(str(exc))


def _chat(byte: Byte, url: str, safe: bool, conversacion: str | None) -> int:
    """La ventana que se queda abierta: preguntá, responde, y sigue esperando.

    Mantiene una sola conversación entre turnos, así que el agente se acuerda de
    lo anterior.

    Ctrl-C no cierra de una: corta lo que esté pasando (la respuesta en curso, o
    la línea a medio escribir) y avisa que otro seguido sí sale. Con un modelo
    local que tarda minutos, Ctrl-C se usa sobre todo para cancelar, y perder la
    sesión entera por querer cortar un run sería peor. Dos seguidos cierran,
    como en Node o en el REPL de Python; Ctrl-D cierra de una.
    """
    _bienvenida(byte, url, interactivo=True)
    print()

    try:
        sesion = Sesion(conversacion or _nueva_conversacion(byte), safe)
    except (RuntimeError, httpx.HTTPError) as exc:
        return _error(str(exc))

    prompt = _color("❯ ", AMBAR)
    armado = False  # un Ctrl-C ya recibido: el próximo cierra
    while sesion.seguir:
        try:
            # El marco va por stdout y no como prompt de `input()`: readline
            # procesa su argumento carácter por carácter para poder reimprimirlo
            # al editar, y en el camino parte la regla con un `\r` — quedaba
            # cortada a la mitad en terminales anchas.
            sys.stdout.write(_marco_del_prompt())
            sys.stdout.flush()
            entrada = input(prompt).strip()
        except EOFError:  # Ctrl-D
            print()
            break
        except KeyboardInterrupt:
            # Ctrl-C en el prompt: descarta la línea. El segundo seguido sale.
            print()
            if armado:
                break
            armado = True
            print(_color("(press Ctrl-C again to exit)", GRIS))
            continue

        # Cualquier cosa que no sea otro Ctrl-C desarma la salida: quien siguió
        # escribiendo no se estaba yendo.
        armado = False

        if not entrada:
            continue

        # La pregunta se vuelve a escribir en gris, arriba de su respuesta.
        # `input()` deja lo tipeado pegado a lo que venga después, y con
        # respuestas largas —o después de una línea de trabajo— no se distingue
        # dónde termina lo que preguntaste y empieza lo que contestó. Repetirla
        # cuesta dos líneas y convierte la sesión en algo que se puede releer.
        _eco_de_la_pregunta(entrada)

        if entrada.startswith("/"):
            _comando_del_chat(byte, entrada, sesion)
            if sesion.seguir:
                print()
            continue

        try:
            datos = _preguntar_en_vivo(
                byte, sesion.conversacion, entrada, sesion.safe, sesion.modelo
            )
        except KeyboardInterrupt:
            # El run sigue del lado de la API; acá solo se deja de esperarlo.
            print(_color("· stopped waiting for the answer", GRIS))
            armado = True  # otro Ctrl-C seguido cierra, como en el prompt
            continue
        except (RuntimeError, httpx.HTTPError) as exc:
            _error(str(exc))
            continue

        if datos.get("status") == "paused":
            _mostrar_pausa(datos)
        else:
            _mostrar_respuesta(datos)
        print()

    # Al salir, el marco quedó dibujado alrededor del prompt vacío: se borra
    # antes de despedirse para no dejar reglas sueltas en la terminal.
    _borrar_marco()
    print(_color("Bye 👋", GRIS))
    return 0


# --- Comandos ---


def _preguntar(byte: Byte, conversacion: str, pregunta: str, safe: bool) -> dict[str, Any]:
    """Una vuelta de pregunta/respuesta, esperando la respuesta completa.

    Usa `?wait=true` en vez del SSE: para un comando de una sola vuelta, seguir
    el stream solo agrega complejidad. El chat, que muestra el texto mientras se
    escribe, sí usa los eventos (`_preguntar_en_vivo`).
    """
    with Estado():
        return byte.pedir(
            "POST",
            f"/conversations/{conversacion}/messages",
            params={"wait": "true"},
            json={"content": pregunta, "safe_mode": safe},
        )


# Qué mostrar en la línea de estado según el nodo del grafo en que esté el run.
# En inglés como el resto de la interfaz, y en gerundio: describe lo que está
# pasando ahora, no lo que se pidió.
POR_PASO = {
    "retrieve_context": "Reading your documents",
    "compact": "Summarizing the conversation",
    "agent": "Thinking",
    "finalize": "Wrapping up",
}

# El nodo `tools` entra *después* del TOOL_CALL_START, así que su verbo genérico
# pisaría el específico ("Searching the web"). Se ignora: la herramienta ya dijo
# algo mejor, y si no dijo nada, el verbo anterior sigue siendo cierto.
PASOS_MUDOS = {"tools"}

POR_HERRAMIENTA = {
    "doc_search": "Searching your documents",
    "web_search": "Searching the web",
    "code_exec": "Running code",
}


def _detalle_de(argumentos: str) -> str:
    """El argumento que vale la pena mostrar al lado del verbo.

    De `{"query": "fastapi"}` saca `fastapi`; del código que va a ejecutar, su
    primera línea. Si no hay nada legible, prefiere no decir nada.
    """
    try:
        datos = json.loads(argumentos)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(datos, dict):
        return ""
    for clave in ("query", "consulta", "q"):
        if isinstance(datos.get(clave), str):
            return _recortar(datos[clave], 48)
    if isinstance(datos.get("code"), str):
        primera = datos["code"].strip().splitlines()[0] if datos["code"].strip() else ""
        return _recortar(primera, 48)
    return ""


def _resumen_de(nombre: str, datos: dict[str, Any]) -> str:
    """Qué decir de una herramienta que ya terminó.

    Del `summary` que manda la API (`results`, `exit_code`, `error`…) sale una
    línea corta: lo que alguien querría saber sin abrir Langfuse.
    """
    if not datos.get("ok", True):
        return {
            "query_vacia": "empty query",
            "embeddings_caidos": "embeddings are down",
            "busqueda_fallida": "the search failed",
            "sandbox_no_disponible": "no sandbox available",
            "codigo_demasiado_largo": "the code was too long",
            "argumentos_invalidos": "invalid arguments",
            "herramienta_desconocida": "unknown tool",
            "ejecucion_fallida": "it failed",
            "rechazado_por_el_usuario": "you rejected it",
        }.get(str(datos.get("error", "")), "failed")

    if nombre == "code_exec":
        partes = ["exit 0"]
        if datos.get("duration_ms") is not None:
            partes.append(f"{int(datos['duration_ms'])}ms")
        if datos.get("truncated"):
            partes.append("output trimmed")
        return ", ".join(partes)

    if "results" in datos:
        cuantos = datos["results"]
        return "nothing found" if not cuantos else f"{cuantos} result{'s' if cuantos != 1 else ''}"
    return "done"


def _linea_de_trabajo(verbo: str, detalle: str, resultado: str, ok: bool) -> None:
    """Una línea de lo que el agente ya hizo, para que quede en el historial.

    Es el contexto que antes se perdía: el spinner lo mostraba y lo borraba. Acá
    queda escrito, como las líneas de trabajo de un agente de código.
    """
    punto = _color("✓" if ok else "✗", VERDE if ok else ROJO)
    linea = f"{punto} {_color(verbo, CREMA)}"
    if detalle:
        linea += f" {_color(detalle, GRIS)}"
    if resultado:
        linea += _color(f" · {resultado}", GRIS)
    print(linea)


def _parrafos(texto: str) -> Iterator[str]:
    """Corta el texto en bloques por línea en blanco.

    Es la unidad con la que se muestra la respuesta: un párrafo entero de una
    vez, en vez de letra por letra. Se ve terminado apenas aparece.
    """
    bloque: list[str] = []
    for linea in texto.splitlines():
        if linea.strip():
            bloque.append(linea)
        elif bloque:
            yield "\n".join(bloque)
            bloque = []
    if bloque:
        yield "\n".join(bloque)


def _preguntar_en_vivo(
    byte: Byte, conversacion: str, pregunta: str, safe: bool, modelo: str = ""
) -> dict[str, Any]:
    """Igual que `_preguntar`, pero siguiendo el run por SSE.

    Devuelve la misma forma que `?wait=true` para que quien llama no distinga:
    `{"message": …}` o `{"status": "paused", …}`. Lo que gana es el contexto: se
    ve qué herramienta usó, con qué argumento y con qué resultado, y cada una
    deja su línea en el historial. El texto se acumula y se muestra armado
    (`_mostrar_respuesta`), no token a token.
    """
    arranque = byte.pedir(
        "POST",
        f"/conversations/{conversacion}/messages",
        json={"content": pregunta, "safe_mode": safe, "model": modelo},
    )
    run_id = arranque["run_id"]

    texto: list[str] = []
    fuentes: list[dict[str, Any]] = []
    pendiente: dict[str, Any] = {}
    llamadas: dict[str, dict[str, str]] = {}  # toolCallId → lo que se sabe de esa llamada
    trabajo: list[str] = []  # nombres de las herramientas que corrieron, en orden
    error = ""
    arrancó = time.monotonic()

    with Estado("Working") as estado:
        for tipo, datos in byte.eventos(f"/runs/{run_id}/events"):
            if tipo == "STEP_STARTED":
                paso = datos.get("stepName", "")
                if paso not in PASOS_MUDOS:
                    estado.cambiar(POR_PASO.get(paso, "Working"))

            elif tipo == "TOOL_CALL_START":
                nombre = datos.get("toolCallName", "")
                llamadas[datos.get("toolCallId", "")] = {"nombre": nombre, "detalle": ""}
                estado.cambiar(POR_HERRAMIENTA.get(nombre, f"Using {nombre}"))

            elif tipo == "TOOL_CALL_ARGS":
                llamada = llamadas.get(datos.get("toolCallId", ""))
                detalle = _detalle_de(datos.get("delta", ""))
                if llamada and detalle:
                    llamada["detalle"] = detalle
                    estado.cambiar(
                        POR_HERRAMIENTA.get(llamada["nombre"], f"Using {llamada['nombre']}"),
                        detalle,
                    )

            elif tipo == "TOOL_CALL_RESULT":
                # La herramienta terminó: su línea sale ya, mientras el modelo
                # sigue pensando. Así se ve avanzar el trabajo, no solo el final.
                llamada = llamadas.get(datos.get("toolCallId", ""), {})
                nombre = llamada.get("nombre", "")
                ok = bool(datos.get("ok", True))
                estado.detener()
                _linea_de_trabajo(
                    POR_HERRAMIENTA.get(nombre, f"Using {nombre}"),
                    llamada.get("detalle", ""),
                    _resumen_de(nombre, datos),
                    ok,
                )
                estado.seguir("Thinking")
                if ok:
                    trabajo.append(nombre)

            elif tipo == "TEXT_MESSAGE_CONTENT":
                # Se acumula y se muestra armado al final: ver letras apareciendo
                # de a una es más lento de leer que un párrafo ya hecho.
                texto.append(datos.get("delta", ""))

            elif tipo in ("STATE_DELTA", "STATE_SNAPSHOT"):
                if datos.get("sources"):
                    fuentes = datos["sources"]
                if datos.get("awaiting_approval"):
                    pendiente = datos["awaiting_approval"]

            elif tipo == "RUN_ERROR":
                error = datos.get("message") or "el run falló"
                break

            elif tipo == "RUN_FINISHED":
                if datos.get("sources"):
                    fuentes = datos["sources"]
                if datos.get("status") == "paused":
                    pendiente = pendiente or datos.get("awaiting_approval", {})
                break

    if error:
        raise RuntimeError(error)

    if pendiente:
        return {"status": "paused", "run_id": run_id, "awaiting_approval": pendiente}

    return {
        "message": {"content": "".join(texto).strip(), "metadata": {"sources": fuentes}},
        "segundos": time.monotonic() - arrancó,
        "herramientas": trabajo,
    }


def _marco_del_prompt() -> str:
    """Las dos reglas que enmarcan dónde se escribe.

    En una conversación larga, el `❯` solo se pierde entre el texto de la
    respuesta anterior: cuesta encontrar dónde termina lo que leíste y empieza
    lo que vas a escribir. Dos reglas de ancho completo lo resuelven sin
    depender del color, que no todas las terminales pintan igual.

    **Las dos van antes del prompt, no una arriba y otra abajo.** `input()`
    escribe donde está el cursor y no puede dibujar por debajo: la regla de
    abajo se pinta primero, se sube con `\033[F` y el prompt queda entre las
    dos. Al apretar Enter el cursor baja a esa segunda regla y la reemplaza —
    por eso `_eco_de_la_pregunta` borra dos líneas y no una.

    Sin terminal no se dibuja: redirigido a un archivo sería ruido.
    """
    if not _en_pantalla():
        return ""
    # Dos columnas de margen, no una. `─` es de ancho "ambiguo" en Unicode: hay
    # terminales que lo pintan doble, y una regla que llena la línea justo hace
    # que envuelva — entonces el `\033[F` sube a la línea equivocada y el prompt
    # queda arriba del marco en vez de adentro. Dos columnas lo evitan sin
    # cambiar el carácter, que es el que da el aspecto que se busca.
    # El mismo ancho que el banner, no el de la terminal: en una pantalla ancha
    # el banner se topa en ANCHO_MAXIMO y unas reglas que siguieran hasta el
    # borde quedarían desalineadas con él, que es justo lo que se nota.
    #
    # Y de borde a borde dentro de ese ancho: medí en un pty que 80 caracteres
    # `─` entran en 80 columnas sin envolver, aunque Unicode los marque de ancho
    # "ambiguo". Un separador a media línea se lee como un adorno; uno completo
    # parte la pantalla, que es para lo que está.
    ancho = min(shutil.get_terminal_size((ANCHO_MAXIMO, 24)).columns, ANCHO_MAXIMO)
    regla = _color("─" * ancho, GRIS)
    # Tres líneas: regla, una vacía donde va el prompt, y la regla de abajo. El
    # `\033[F` deja el cursor en la **columna 0** de la línea anterior, así que
    # sin la línea vacía el `❯` se escribiría encima de la primera regla.
    return f"{regla}\n\n{regla}\033[F"


def _borrar_marco() -> None:
    """Quita el marco del prompt cuando se sale sin escribir nada.

    Ctrl-C y Ctrl-D dejan el cursor sobre la regla de abajo, con el prompt y la
    de arriba encima: sin esto quedan tres líneas sueltas en la terminal después
    del "Bye".
    """
    if not _en_pantalla():
        return
    # Una de más: `\033[F` no baja del borde superior de la pantalla, así que
    # sobrar es inocuo, mientras que quedarse corto deja media regla colgada
    # debajo del "Bye".
    sys.stdout.write("\r\033[2K" + "\033[F\033[2K" * 3)
    sys.stdout.flush()


def _eco_de_la_pregunta(pregunta: str) -> None:
    """Reescribe la pregunta como un bloque con fondo, arriba de su respuesta.

    El `input()` ya dejó lo tipeado en pantalla, pegado a lo que venga después:
    con una respuesta larga no se distingue dónde termina lo que preguntaste y
    empieza lo que contestó. Acá esa línea se **reemplaza** por un bloque con
    fondo propio, que es lo que permite recorrer una sesión larga hacia arriba
    saltando de pregunta en pregunta.

    **Cuántas líneas subir.** Al apretar Enter la terminal hace eco de un `\r\n`,
    así que el cursor ya está en la línea de abajo: `\033[F` una sola vez sube a
    la del prompt, la borra, y el `print` siguiente agrega otra — la pregunta
    termina apareciendo dos veces. Hay que subir **una por cada línea que ocupó
    lo tipeado**, que con el ajuste al ancho de la terminal puede ser más de una.

    Si no se puede calcular con certeza —una pregunta multilínea— no se borra
    nada: equivocarse se come la respuesta anterior, que es mucho peor que un
    eco de más.

    Solo en pantalla: redirigido a un archivo, el bloque sería ruido.
    """
    if not _en_pantalla():
        return

    ancho = shutil.get_terminal_size((80, 24)).columns
    if "\n" not in pregunta:
        # El marco son tres líneas —regla, prompt, regla— y al apretar Enter el
        # cursor queda sobre la de abajo. Hay que borrar esa, lo tipeado (que
        # ocupa más de una línea si no entró en el ancho), y la regla de arriba.
        #
        # Se borra una línea de más y se acepta: `\033[F` no baja del borde
        # superior de la pantalla, así que sobrar es inocuo —el escape no hace
        # nada— mientras que quedarse corto deja media regla colgada sobre la
        # pregunta, que es el resto que se veía.
        sys.stdout.write("\r\033[2K")
        ocupadas = max(1, -(-(len(pregunta) + 2) // max(1, ancho)))
        sys.stdout.write("\033[F\033[2K" * (ocupadas + 2))

    print()
    for linea in _envolver(pregunta, ancho - 4) or [""]:
        # El fondo se pinta hasta el borde del bloque, no solo detrás del texto:
        # un rectángulo se lee como una unidad, un fondo irregular no.
        relleno = " " * max(0, ancho - 4 - len(linea))
        print(f"{FONDO}{CREMA}  {linea}{relleno}  {FIN}")
    print()


def _envolver(texto: str, ancho: int) -> list[str]:
    """Corta el texto en líneas que entren en `ancho`, sin partir palabras."""
    import textwrap

    return textwrap.wrap(texto, max(20, ancho)) or [texto[:ancho]]


def _mostrar_pausa(datos: dict[str, Any]) -> None:
    """Modo seguro: el run espera una decisión humana. Se muestra el código y se
    resuelve con `byte approve` / `byte reject`."""
    pendiente = datos["awaiting_approval"]
    print(_color("⚠ Byte wants to run code and is waiting for your call", AMBAR))
    for codigo in pendiente.get("codes") or [pendiente.get("code", "")]:
        print(_color(codigo, GRIS))
    print()
    print(f"  byte approve {datos['run_id']} {pendiente['resume_token']}")
    print(f"  byte reject  {datos['run_id']} {pendiente['resume_token']}")


def _mostrar_respuesta(datos: dict[str, Any]) -> None:
    """La respuesta ya terminada, en bloques, y debajo de qué salió.

    Por párrafos y no token a token: un bloque completo se lee de un vistazo,
    mientras que ver letras apareciendo obliga a esperar a que pare.
    """
    mensaje = datos["message"]
    contenido = mensaje["content"]
    # Redirigido a un archivo va el texto pelado: el pie y el aire son para leer
    # en la terminal, y ensuciarían la salida de un script.
    con_adornos = _en_pantalla()
    if con_adornos:
        if datos.get("herramientas"):
            print()  # aire entre las líneas de trabajo y la respuesta
        for i, parrafo in enumerate(_parrafos(contenido)):
            if i:
                print()
            print(parrafo)
    else:
        print(contenido)
        return

    # El pie: de dónde salió y cuánto costó. Es el contexto que el spinner
    # mostraba y borraba.
    pie = []
    fuentes = mensaje["metadata"].get("sources") or []
    if fuentes:
        nombres = sorted({f.get("filename") or f.get("url", "") for f in fuentes} - {""})
        if nombres:
            pie.append("Sources: " + " · ".join(nombres))
    if datos.get("herramientas"):
        pie.append(" · ".join(datos["herramientas"]))
    # Por debajo de un segundo el número no dice nada; se omite en vez de "0s".
    if datos.get("segundos", 0) >= 1:
        pie.append(f"Worked for {_duracion(datos['segundos'])}")
    if pie:
        print()
        print(_color("✻ " + "  ·  ".join(pie), GRIS))


def _duracion(segundos: float) -> str:
    """`26s`, o `2m 5s` cuando pasa del minuto.

    Un modelo local pasa el minuto seguido, y `143s` obliga a dividir mentalmente
    para saber si eso fue mucho.
    """
    if segundos < 60:
        return f"{segundos:.0f}s"
    minutos, resto = divmod(int(segundos), 60)
    return f"{minutos}m {resto}s" if resto else f"{minutos}m"


def cmd_ask(byte: Byte, args: argparse.Namespace) -> int:
    """Una pregunta al agente, esperando la respuesta completa."""
    conversacion = args.conversation or byte.pedir("POST", "/conversations", json={})["id"]
    datos = _preguntar(byte, conversacion, args.pregunta, args.safe)

    if datos.get("status") == "paused":
        _mostrar_pausa(datos)
        return 2

    _mostrar_respuesta(datos)
    if args.conversation is None and sys.stdout.isatty():
        print(_color(f"\nConversation: {conversacion}", GRIS))
    return 0


def cmd_resume(byte: Byte, args: argparse.Namespace) -> int:
    """Aprueba o rechaza una ejecución pendiente."""
    byte.pedir(
        "POST",
        f"/runs/{args.run_id}/resume",
        json={"resume_token": args.token, "approve": args.aprobar},
    )
    print(_color("✓ " + ("approved" if args.aprobar else "rejected"), VERDE))
    print(_color("The run continues — see it with `byte ask -c <id>`", GRIS))
    return 0


def cmd_search(byte: Byte, args: argparse.Namespace) -> int:
    """Búsqueda híbrida en los documentos, sin pasar por el agente."""
    datos = byte.pedir("POST", "/search", json={"query": args.consulta, "top_k": args.top_k})
    if not datos["results"]:
        print(_color("Nothing in your documents matched that.", GRIS))
        return 0
    for i, r in enumerate(datos["results"], start=1):
        print(f"{_color(f'[{i}] {r["filename"]}', AMBAR)}  {_color(f'{r["score"]:.3f}', GRIS)}")
        print(f"    {r['snippet']}")
    return 0


def cmd_run(byte: Byte, args: argparse.Namespace) -> int:
    """Ejecuta un archivo de Python en el sandbox, sin pasar por el modelo."""
    ruta = Path(args.archivo)
    if not ruta.is_file():
        return _error(f"{ruta} does not exist")
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
        print(_color("[output trimmed]", GRIS), file=sys.stderr)
    # El exit_code del sandbox se propaga: así `byte run` encadena en un script.
    return int(datos["exit_code"])


def cmd_docs_add(byte: Byte, args: argparse.Namespace) -> int:
    ruta = Path(args.archivo)
    if not ruta.is_file():
        return _error(f"{ruta} does not exist")
    with ruta.open("rb") as archivo:
        datos = byte.pedir("POST", "/documents", files={"file": (ruta.name, archivo)})
    print(_color(f"✓ {datos['filename']} uploaded", VERDE))
    print(_color("  indexing… follow it with `byte docs list`", GRIS))
    return 0


def cmd_docs_list(byte: Byte, _args: argparse.Namespace) -> int:
    datos = byte.pedir("GET", "/documents")
    if not datos["items"]:
        print(_color("No documents yet.", GRIS))
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
    print(_color("✓ removed", VERDE))
    return 0


def cmd_status(byte: Byte, _args: argparse.Namespace) -> int:
    salud = byte.pedir("GET", "/health/details")
    ok = salud["status"] == "ok"
    print(_color("● " + ("online, running local" if ok else "degraded"), VERDE if ok else AMBAR))
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


def cmd_login(byte: Byte, args: argparse.Namespace) -> int:
    """Entra como usuario y guarda la sesión.

    Sin esto el CLI solo sabía de la API key, que identifica a la instancia:
    desde la terminal no había forma de ser vos mismo. Con sesión iniciada, lo
    que creás es tuyo y nadie más lo ve.
    """
    url, _ = _config()
    email = (args.email or input("email: ")).strip()
    # La contraseña por `getpass`, que no la muestra ni la deja en el historial
    # del shell. Por eso tampoco hay un `--password`.
    password = getpass.getpass("contraseña: ")
    if not email or not password:
        return _error("hacen falta el email y la contraseña")

    # El login va con la API key y **no** con el token de la sesión anterior: el
    # 401 de "contraseña incorrecta" es indistinguible del de "access vencido",
    # así que `pedir` intentaba renovar y gastaba una rotación del refresh por
    # cada intento fallido. Entrar no debería depender de estar ya adentro.
    _, clave = _config()
    try:
        with httpx.Client(
            base_url=url.rstrip("/") + "/api/v1",
            headers={"X-API-Key": clave} if clave else {},
            timeout=30.0,
        ) as anonimo:
            respuesta = anonimo.post("/auth/login", json={"email": email, "password": password})
        if respuesta.status_code >= 400:
            return _error(_mensaje_de_error(respuesta))
        datos = respuesta.json()
    except httpx.HTTPError as exc:
        return _error(f"no se pudo entrar: {type(exc).__name__}")

    sesion.guardar(url, datos["access_token"], datos["refresh_token"], email)
    print(_color(f"✓ hola, {email}", VERDE))
    print(_color(f"  la sesión queda en {sesion.ruta_visible()}", GRIS))
    return 0


def cmd_logout(_byte: Byte, _args: argparse.Namespace) -> int:
    """Cierra la sesión acá y en el servidor.

    Borrar el archivo local no alcanza: el refresh seguiría valiendo 14 días
    para quien lo tuviera. Se revoca primero y se borra después.
    """
    url, _ = _config()
    guardada = sesion.leer(url)
    if guardada is None:
        print(_color("no había sesión iniciada", GRIS))
        return 0

    # Se habla con la API sin el `Byte` de afuera: su cliente ya tiene el token
    # cargado y acá hace falta mandar solo el refresh.
    #
    # `rstrip("/")` no es cosmético: con `BYTE_URL` terminada en barra la URL
    # quedaba `//api/v1/auth/logout` → 404, y como `httpx.post` no levanta por
    # status se imprimía "sesión cerrada" mientras el refresh seguía vivo 14
    # días. `sesion.leer/borrar` sí normalizan, así que la parte local
    # funcionaba y nada delataba el fallo.
    revocado = False
    try:
        respuesta = httpx.post(
            f"{url.rstrip('/')}/api/v1/auth/logout",
            json={"refresh_token": guardada.get("refresh_token", "")},
            timeout=10.0,
        )
        revocado = respuesta.status_code < 400
    except httpx.HTTPError:
        revocado = False

    sesion.borrar(url)
    if revocado:
        print(_color("✓ sesión cerrada", VERDE))
        return 0
    # Borrar el archivo local es lo único que se pudo hacer: hay que decirlo,
    # porque el refresh sigue valiendo para quien lo tenga.
    print(_color("✓ sesión borrada de esta máquina", VERDE))
    print(_color("  ⚠ el servidor no confirmó la revocación: el token sigue válido allá", AMBAR))
    return 0


def cmd_whoami(byte: Byte, _args: argparse.Namespace) -> int:
    """Con quién se está trabajando."""
    if byte.email is None:
        print(_color("sin sesión: se usa la API key de la instancia", GRIS))
        print(_color("  `byte login` para entrar como usuario", GRIS))
        return 0
    try:
        yo = byte.pedir("GET", "/me")
    except RuntimeError as exc:
        return _error(f"{exc} — probá `byte login` de nuevo")
    print(f"{_color(yo['email'], CREMA)}")
    print(_color(f"  desde {yo['created_at'][:10]}", GRIS))
    return 0


def cmd_chat(byte: Byte, args: argparse.Namespace) -> int:
    url, _ = _config()
    return _chat(byte, url, args.safe, args.conversation)


# --- Parseo ---


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="byte", description="Byte: your local AI agent, from the terminal."
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

    p = sub.add_parser("login", help="entrar como usuario y guardar la sesión")
    p.add_argument("email", nargs="?", help="si no se pasa, se pregunta")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("logout", help="cerrar la sesión acá y en el servidor")
    p.set_defaults(func=cmd_logout)

    p = sub.add_parser("whoami", help="con quién se está trabajando")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("chat", help="abrir el chat interactivo (lo mismo que `byte` a secas)")
    p.add_argument("-c", "--conversation", help="seguir una conversación existente")
    p.add_argument(
        "--safe", action="store_true", help="pedir confirmación antes de ejecutar código"
    )
    p.set_defaults(func=cmd_chat)

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
        return _error("missing BYTE_API_KEY (or a .env holding it)")

    try:
        with Byte(url, clave) as byte:
            if args.comando is None:
                # Con terminal, `byte` abre el chat y se queda ahí. Sin ella
                # (un pipe, un script) solo se presenta: un REPL leyendo de
                # stdin redirigido no es lo que nadie espera.
                if _interactiva():
                    return _chat(byte, url, safe=False, conversacion=None)
                return _bienvenida(byte, url)
            return int(args.func(byte, args))
    except httpx.ConnectError:
        return _error(f"{url} is not answering — is the API up?")
    except httpx.TimeoutException:
        return _error("the API took too long")
    except RuntimeError as exc:
        return _error(str(exc))
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
