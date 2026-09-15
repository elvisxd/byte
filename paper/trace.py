"""El razonamiento de la sesión, para poder verlo desde fuera.

═══ QUÉ ES, Y EN QUÉ SE DIFERENCIA DEL HISTORIAL ═══

El historial en papel dice **qué decidió** el agente: entró acá, salió allá, con
esta razón sellada. El trace dice **cómo llegó a decidirlo**: qué miró, en qué
orden, qué le devolvió cada herramienta y qué escribió por el camino.

Son dos cosas distintas y por eso viajan aparte. El historial es el experimento
—se conserva, se compara, decide si un eje sirve—; el trace es la ventana para
mirar mientras corre, y pierde valor en cuanto la sesión termina.

⚠ POR ESO EL TRACE SÍ CADUCA Y EL HISTORIAL NO. Guardar el razonamiento
completo de cada vuelta para siempre llenaría Redis con texto que nadie va a
releer: lo que importa de una sesión vieja ya está en el historial, sellado. El
trace vive unas horas y se va.

⚠ Y POR ESO ES BEST-EFFORT. Si publicar el trace falla, la sesión sigue: perder
la ventana para mirar no es perder el experimento. Lo contrario —abortar una
sesión porque no se pudo enseñar lo que estaba haciendo— sería dejar que la
comodidad mande sobre el trabajo.

═══ LO QUE NO SE PUBLICA ═══

El resultado completo de cada herramienta puede ser enorme —200 velas son
decenas de KB— y no aporta nada mirándolo en vivo. Se recorta. Lo que interesa
es la SECUENCIA: miró el mercado, pensó, abrió, volvió a mirar.
"""

import json
import os
import threading
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

# Cuántos pasos se conservan. Una vuelta del agente son ~6 pasos, así que esto
# cubre las últimas ~30 vueltas: bastante más de lo que dura una sesión.
TOPE_PASOS = 200

# Cuánto de un resultado de herramienta viaja. Lo justo para ver qué devolvió
# sin mandar 200 velas por la red en cada paso.
RECORTE = 400

# Cuánto de un PENSAMIENTO viaja. Bastante más que un resultado, porque es lo
# que se audita: si el modelo recorre los ejes o repite una plantilla se ve
# ahí, y 400 caracteres cortan antes de llegar a la conclusión. Medido con
# qwen3:14b el 2026-09-14: un pensamiento entero son ~3.400 caracteres.
RECORTE_PENSAMIENTO = 2000


class TraceDeSesion:
    """Acumula los eventos del agente y los empuja al panel.

    Implementa el protocolo `Emitter` del grafo, así que se inyecta por config
    y el agente no sabe que existe.

    ⚠ CON LOCK. El emitter lo llaman los nodos del grafo, y aunque hoy corren en
    el mismo hilo, `publicar()` se llama desde el bucle de la sesión: dos
    accesos al deque desde hilos distintos es exactamente el fallo que aparece
    una vez cada cien sesiones y no se puede reproducir.
    """

    def __init__(
        self,
        *,
        sesion_id: str,
        modelo: "str | Callable[[], str]",
        simbolo: str,
        archivo: str = "",
        sin_panel: bool = False,
    ) -> None:
        self.sesion_id = sesion_id
        # Callable para el brazo remoto: el modelo que contesta cambia con el
        # relevo, y la traza tiene que decir cuál es AHORA. Ver agent/relevo.py.
        self.modelo = modelo
        # `archivo`: además de publicar, se escribe en disco —es lo que lee
        # paper/rubrica.py—. `sin_panel`: el panel enseña UNA traza, y los
        # brazos remotos no cuentan la suya ahí (CRITERIO_COMPARACION.md).
        self.archivo = archivo
        self.sin_panel = sin_panel
        self.simbolo = simbolo
        self.empezo = datetime.now(UTC).isoformat()
        self.vuelta = 0
        self._pasos: deque[dict[str, Any]] = deque(maxlen=TOPE_PASOS)
        self._lock = threading.Lock()
        # La última llamada completa —herramienta, argumentos, resultado— y el
        # paso donde vive su contador. Ver `_es_repeticion`.
        self._ultima_llamada: tuple[tuple[str, str, str, int], dict[str, Any]] | None = None

    # ── el protocolo que espera el grafo ──────────────────────────────────
    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        paso = self._traducir(event_type, data)
        if paso is None:
            return
        with self._lock:
            # ⚠ EL TEXTO SE JUNTA AL ENTRAR, NO AL PUBLICAR. El modelo emite
            # token a token, así que una respuesta de 500 tokens son 500 eventos
            # y el deque de 200 expulsaba todo lo demás: medido, una sesión que
            # llamó a `mirar_mercado` y abrió una operación terminaba con UN
            # paso de texto y ninguna herramienta. Juntarlo al publicar no
            # servía — para entonces lo que había que juntar ya se había caído.
            ultimo = self._pasos[-1] if self._pasos else None
            # El pensamiento también llega token a token, y se junta igual que
            # el texto — con su propio recorte, más generoso.
            if (
                paso["tipo"] in ("texto", "pensamiento")
                and ultimo is not None
                and ultimo["tipo"] == paso["tipo"]
                and ultimo["vuelta"] == self.vuelta
            ):
                tope = RECORTE_PENSAMIENTO if paso["tipo"] == "pensamiento" else RECORTE
                ultimo["texto"] = _recortar(str(ultimo["texto"]) + str(paso["texto"]), tope)
                return
            if paso["tipo"] == "resultado" and self._es_repeticion(paso):
                return
            self._pasos.append({"en": datetime.now(UTC).isoformat(), "vuelta": self.vuelta, **paso})

    def _es_repeticion(self, resultado: dict[str, Any]) -> bool:
        """Una llamada idéntica a la anterior no se guarda: se CUENTA.

        Misma herramienta, mismos argumentos, misma respuesta, misma vuelta. Se
        descartan los dos pasos que acaban de entrar —herramienta y argumentos—,
        no se guarda el resultado, y el paso de herramienta de la PRIMERA vez
        suma uno en `veces`.

        ⚠ CONTAR, NO BORRAR. Medido el 2026-09-14 con qwen3:14b: 44 razones en
        una sesión, 31 de ellas la misma frase palabra por palabra, apuntando 32
        veces al mismo nivel ya ocupado. Ese `×31` ES el hallazgo —que el modelo
        no reacciona a los rechazos— y una traza que enseñara un intento cuando
        hubo treinta y uno lo habría tapado. Pero treinta y una copias del mismo
        paso son ruido, y con el tope de 200 pasos expulsaban el resto de la
        sesión: la de esa mañana terminó con 200 justos, es decir, recortada.

        Solo se colapsa dentro de la misma vuelta a propósito: repetir en la
        vuelta 3 lo que ya se repitió en la 2 es otro dato, y se quiere ver.
        """
        if len(self._pasos) < 2:
            return False
        argumentos, herramienta = self._pasos[-1], self._pasos[-2]
        if argumentos["tipo"] != "argumentos" or herramienta["tipo"] != "herramienta":
            return False
        llamada = (herramienta["nombre"], argumentos["texto"], resultado["texto"], self.vuelta)
        anterior = self._ultima_llamada
        if anterior is None or anterior[0] != llamada:
            self._ultima_llamada = (llamada, herramienta)
            return False
        self._pasos.pop()
        self._pasos.pop()
        anterior[1]["veces"] = anterior[1].get("veces", 1) + 1
        return True

    def _traducir(self, tipo: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """De evento AG-UI a algo que se pueda leer en una página.

        Solo lo que cuenta la historia. `STATE_DELTA` y compañía son ruido para
        quien mira: dicen cómo cambió la estructura interna, no qué hizo el
        agente.
        """
        if tipo == "TOOL_CALL_START":
            return {"tipo": "herramienta", "nombre": data.get("toolCallName", "?")}
        if tipo == "TOOL_CALL_ARGS":
            return {"tipo": "argumentos", "texto": _recortar(data.get("delta", ""))}
        if tipo == "TOOL_CALL_RESULT":
            # ⚠ EL RESULTADO NO VIENE EN `content`. El grafo emite
            # `{toolCallId, ok, **result.summary}` —el resumen de la
            # herramienta, no su texto—, así que buscar `content` devolvía
            # siempre vacío: el trace enseñaba las llamadas sin lo que
            # contestaron. Medido en la primera sesión con trace: cero pasos de
            # resultado sobre una sesión que abrió y cerró una operación.
            #
            # El summary es además lo que conviene enseñar: `{"id": 1, "eje":
            # "range-sweep", "precio": 76966.51}` dice qué pasó sin arrastrar
            # las 200 velas que el modelo recibió.
            resumen = {k: v for k, v in data.items() if k != "toolCallId"}
            return {"tipo": "resultado", "texto": _recortar(resumen)}
        if tipo == "TEXT_MESSAGE_CONTENT":
            # El texto llega en trozos; se junta en el último paso si ya había
            # uno de texto, para no convertir cada token en una fila.
            return {"tipo": "texto", "texto": str(data.get("delta", ""))}
        if tipo == "THINKING_TEXT_MESSAGE_CONTENT":
            # Igual que el texto, pero es lo que el modelo pensó ANTES de
            # decidir. Solo llega con el razonamiento encendido (ver `build_llm`).
            return {"tipo": "pensamiento", "texto": str(data.get("delta", ""))}
        if tipo == "RUN_ERROR":
            return {"tipo": "error", "texto": _recortar(data.get("message", ""))}
        return None

    # ── lo que se publica ─────────────────────────────────────────────────
    def instantanea(self, *, viva: bool = True) -> dict[str, Any]:
        with self._lock:
            pasos = list(self._pasos)
        return {
            "sesionId": self.sesion_id,
            "modelo": self.modelo() if callable(self.modelo) else self.modelo,
            "simbolo": self.simbolo,
            "empezo": self.empezo,
            "actualizado": datetime.now(UTC).isoformat(),
            "vuelta": self.vuelta,
            "viva": viva,
            "pasos": _juntar_texto(pasos),
        }

    def publicar(self, *, viva: bool = True) -> bool:
        """Empuja el trace. Devuelve si se pudo; nunca lanza.

        Ver la cabecera: perder la ventana para mirar no es perder el
        experimento, así que esto jamás puede abortar una sesión.
        """
        cuerpo = json.dumps(self.instantanea(viva=viva), ensure_ascii=False).encode("utf-8")
        en_disco = self._a_archivo(cuerpo) if self.archivo else False
        destino = os.environ.get("PANEL_URL", "")
        if self.sin_panel or not destino:
            return en_disco
        pedido = urllib.request.Request(  # noqa: S310 — destino fijado por entorno
            destino.rstrip("/") + "/api/papel/trace",
            data=cuerpo,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.environ.get('PANEL_TOKEN', '')}",
            },
        )
        try:
            with urllib.request.urlopen(pedido, timeout=10) as r:  # noqa: S310
                r.read()
        except (OSError, urllib.error.URLError, ValueError):
            return False
        return True

    def _a_archivo(self, cuerpo: bytes) -> bool:
        # Escribe entero y renombra: quien lea a mitad de escritura ve el
        # anterior completo, no medio JSON.
        try:
            temporal = self.archivo + ".tmp"
            os.makedirs(os.path.dirname(self.archivo) or ".", exist_ok=True)
            with open(temporal, "wb") as f:
                f.write(cuerpo)
            os.replace(temporal, self.archivo)
        except OSError:
            return False
        return True


def _recortar(valor: Any, tope: int = RECORTE) -> str:
    texto = valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False)
    return texto if len(texto) <= tope else texto[:tope] + f"… (+{len(texto) - tope})"


def _juntar_texto(pasos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Junta los trozos de texto consecutivos en un solo paso.

    El modelo emite el texto token a token. Sin esto, un párrafo de respuesta
    serían doscientas filas de una letra cada una — ilegible y pesado de mandar.
    """
    juntos: list[dict[str, Any]] = []
    for paso in pasos:
        ultimo = juntos[-1] if juntos else None
        if (
            paso.get("tipo") in ("texto", "pensamiento")
            and ultimo is not None
            and ultimo.get("tipo") == paso.get("tipo")
            and ultimo.get("vuelta") == paso.get("vuelta")
        ):
            tope = RECORTE_PENSAMIENTO if paso.get("tipo") == "pensamiento" else RECORTE
            ultimo["texto"] = _recortar(str(ultimo["texto"]) + str(paso["texto"]), tope)
            continue
        juntos.append(dict(paso))
    return juntos
