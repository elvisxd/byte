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
from datetime import UTC, datetime
from typing import Any

# Cuántos pasos se conservan. Una vuelta del agente son ~6 pasos, así que esto
# cubre las últimas ~30 vueltas: bastante más de lo que dura una sesión.
TOPE_PASOS = 200

# Cuánto de un resultado de herramienta viaja. Lo justo para ver qué devolvió
# sin mandar 200 velas por la red en cada paso.
RECORTE = 400


class TraceDeSesion:
    """Acumula los eventos del agente y los empuja al panel.

    Implementa el protocolo `Emitter` del grafo, así que se inyecta por config
    y el agente no sabe que existe.

    ⚠ CON LOCK. El emitter lo llaman los nodos del grafo, y aunque hoy corren en
    el mismo hilo, `publicar()` se llama desde el bucle de la sesión: dos
    accesos al deque desde hilos distintos es exactamente el fallo que aparece
    una vez cada cien sesiones y no se puede reproducir.
    """

    def __init__(self, *, sesion_id: str, modelo: str, simbolo: str) -> None:
        self.sesion_id = sesion_id
        self.modelo = modelo
        self.simbolo = simbolo
        self.empezo = datetime.now(UTC).isoformat()
        self.vuelta = 0
        self._pasos: deque[dict[str, Any]] = deque(maxlen=TOPE_PASOS)
        self._lock = threading.Lock()

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
            if (
                paso["tipo"] == "texto"
                and ultimo is not None
                and ultimo["tipo"] == "texto"
                and ultimo["vuelta"] == self.vuelta
            ):
                ultimo["texto"] = _recortar(str(ultimo["texto"]) + str(paso["texto"]))
                return
            self._pasos.append({"en": datetime.now(UTC).isoformat(), "vuelta": self.vuelta, **paso})

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
        if tipo == "RUN_ERROR":
            return {"tipo": "error", "texto": _recortar(data.get("message", ""))}
        return None

    # ── lo que se publica ─────────────────────────────────────────────────
    def instantanea(self, *, viva: bool = True) -> dict[str, Any]:
        with self._lock:
            pasos = list(self._pasos)
        return {
            "sesionId": self.sesion_id,
            "modelo": self.modelo,
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
        destino = os.environ.get("PANEL_URL", "")
        if not destino:
            return False
        cuerpo = json.dumps(self.instantanea(viva=viva), ensure_ascii=False).encode("utf-8")
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


def _recortar(valor: Any) -> str:
    texto = valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False)
    return texto if len(texto) <= RECORTE else texto[:RECORTE] + f"… (+{len(texto) - RECORTE})"


def _juntar_texto(pasos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Junta los trozos de texto consecutivos en un solo paso.

    El modelo emite el texto token a token. Sin esto, un párrafo de respuesta
    serían doscientas filas de una letra cada una — ilegible y pesado de mandar.
    """
    juntos: list[dict[str, Any]] = []
    for paso in pasos:
        ultimo = juntos[-1] if juntos else None
        if (
            paso.get("tipo") == "texto"
            and ultimo is not None
            and ultimo.get("tipo") == "texto"
            and ultimo.get("vuelta") == paso.get("vuelta")
        ):
            ultimo["texto"] = _recortar(str(ultimo["texto"]) + str(paso["texto"]))
            continue
        juntos.append(dict(paso))
    return juntos
