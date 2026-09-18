"""Qué modelos tiene cada proveedor, cuáles sirven y cuáles quedaron descartados.

═══ POR QUÉ EXISTE ═══

`agent/relevo.py` ya descubre modelos inservibles: manda a cuarentena
`permanente` el 404 y el 402 para no gastar una llamada por vuelta averiguando
lo mismo. Pero esa cuarentena vive en memoria y su propio mensaje lo dice —«no
vuelve en esta sesión»—, así que **el descubrimiento se pierde en cada
reinicio**.

Y lo que se pierde no es poco. Medido el 2026-09-18 a las 12:00 EDT, primera
vuelta del brazo `openrouter`:

    openrouter/z-ai/glm-5.2:free agotado (permanente) · 404
    'No endpoints found that support tool use'

Ese modelo NO ES QUE ESTUVIERA OCUPADO: no tiene llamada a herramientas, y este
agente vive de llamarlas (`precio`, `velas`, `registrar`). No va a servir hoy ni
en un mes. Sin embargo estaba en la lista del brazo, sale en `/v1/models` del
proveedor, y tras el reinicio siguiente lo habría vuelto a intentar —una llamada
fallida por vuelta— hasta que alguien leyera el log de madrugada. Lo quitó una
persona a mano; debería quitarlo el sistema.

Así que el descarte se guarda en disco, en el volumen, junto a los registros.

═══ QUÉ SE GUARDA Y QUÉ NO ═══

⚠ NO TODO FALLO ES UN VEREDICTO, Y CONFUNDIRLOS BORRA MODELOS BUENOS. Un 429 es
cuota, un 503 es carga ajena y un 413 es el tamaño de ESTA petición (el brazo
Groq da 413 varias veces al día contra su tope de 8.000 y es el brazo con más
muestra de la comparación). Ninguno dice nada del modelo. Acá entra solo lo
estructural: el 404 y el 402 que `relevo.py` ya separó como `permanente`.

⚠ Y DENTRO DE LO PERMANENTE HAY GRADOS, que es la otra mitad del problema:

  `sin_herramientas`  404 «no endpoints found that support tool use». Estructural
                      de verdad: nunca se revisa. Es el caso del GLM.
  `sin_acceso`        404 «does not exist or you do not have access». Puede ser
                      un typo (no vuelve nunca) o un catálogo que cambió (puede
                      volver). Se revisa al mes: equivocarse acá cuesta una
                      llamada fallida, equivocarse al revés cuesta un modelo.
  `hay_que_pagar`     402. Depende de la cuenta, no del modelo; no se revisa sola
                      porque lo que tiene que cambiar es la cuenta.
  `se_cobra`          un id de OpenRouter sin el sufijo `:free`. No lo descubre
                      un fallo: lo decide la regla del proyecto —no pagar una IA
                      antes de saber si es rentable— y nunca se revisa.

La clasificación es CONSERVADORA A PROPÓSITO: solo las frases conocidas caen en
`sin_herramientas`, y cualquier otro 404 cae en `sin_acceso`, que sí se revisa.
Fallar hacia «revisable» cuesta una petición al mes; fallar hacia «nunca» tira
un modelo que funcionaba y nadie se enteraría.

⚠ QUE UN MODELO REAPAREZCA EN `/v1/models` NO LEVANTA SU DESCARTE. Suena
razonable y está mal: el GLM de arriba SIGUE EN EL CATÁLOGO de OpenRouter y sigue
sin soportar herramientas, y los 82 modelos que lista NVIDIA son el catálogo
PÚBLICO y no el de la cuenta —el primero que se sondeó dio 404 «Not found for
account …»—. El catálogo dice qué existe, no qué se puede usar. Solo el tiempo
(`revisable_desde`) levanta un descarte.

═══ QUÉ SÍ CUENTA COMO «SIRVE» ═══

Que haya contestado una vuelta de verdad, con herramientas y 7-10k de entrada.
No una sonda de 16 tokens: eso ya lo hace `scripts/sondear-proveedor.sh` del
dashboard y no distingue al GLM —una sonda sin herramientas le habría dado 200—.
Se anota en la transición, cuando el relevo cambia de modelo, así que son unas
pocas escrituras por proceso y no una por vuelta.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Cuánto tarda en revisarse un `sin_acceso`. Un mes es el orden en el que cambia
# el catálogo gratis de un intermediario: OpenRouter mueve modelos dentro y fuera
# de `:free` sin avisar. Más corto gasta llamadas en typos; más largo se pierde
# un modelo que volvió.
REVISION_SIN_ACCESO = timedelta(days=30)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS modelos (
    proveedor       TEXT    NOT NULL,   -- gemini | groq | cerebras | nvidia | mistral | openrouter
    -- ⚠ EL ID COMPLETO, COMO LO RECIBE `build_llm`, con prefijo y con `:free`.
    -- `openrouter/deepseek/deepseek-chat-v3-0324:free` y el mismo sin sufijo son
    -- dos filas distintas porque son dos facturas distintas: guardar el id
    -- «limpio» juntaría la variante gratis con la de pago bajo un veredicto.
    modelo          TEXT    NOT NULL,
    veredicto       TEXT    NOT NULL,   -- sin_probar | sirve | descartado
    motivo          TEXT,               -- ver la cabecera del módulo
    codigo          INTEGER,            -- el HTTP que lo dijo, si lo hubo
    evidencia       TEXT,               -- el mensaje del proveedor, recortado
    -- Cuándo se decidió el veredicto actual, y desde cuándo se puede volver a
    -- probar. NULL en un descartado significa NUNCA: ver la cabecera.
    decidido_en     TEXT,
    revisable_desde TEXT,
    -- El catálogo del proveedor: qué dijo `/v1/models` y cuándo. Es información,
    -- no permiso — no levanta descartes.
    --
    -- ⚠ ESTAS TRES LAS ESCRIBE SOLO `visto_en_catalogo`, y la distinción importa.
    -- `en_catalogo = 0` con `visto_ultimo` puesto significa «lo listaba y
    -- desapareció», que es un aviso de verdad para el brazo de OpenRouter. Con
    -- `visto_ultimo` en NULL significa «nunca se sondeó este proveedor», que no
    -- es ningún aviso. Cuando `_anotar` también las tocaba, el parte gritaba «ya
    -- no sale en /v1/models» de los seis modelos de Gemini por no haberlos
    -- sondeado nunca.
    visto_primero   TEXT,
    visto_ultimo    TEXT,
    en_catalogo     INTEGER NOT NULL DEFAULT 0,
    -- Que haya servido una vuelta de verdad, y cuántas veces. Es lo que separa
    -- «existe» de «funciona».
    funciono_en     TEXT,
    veces_funciono  INTEGER NOT NULL DEFAULT 0,
    veces_fallo     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (proveedor, modelo)
);
"""

# Las frases conocidas de «este modelo no tiene herramientas», que es el único
# descarte que no se revisa nunca. La lista es cerrada a propósito: ver el ⚠ de
# la cabecera sobre por qué la clasificación falla hacia «revisable».
#
# La primera es la de OpenRouter, medida el 2026-09-18 con `z-ai/glm-5.2:free`.
# Nótese que la negación va al principio («no endpoints found that support…») y
# no junto al verbo: un `"not support" in texto` la habría dejado pasar.
_SIN_HERRAMIENTAS = (
    "no endpoints found that support tool",
    "does not support tool",
    "doesn't support tool",
    "does not support function calling",
    "doesn't support function calling",
    "tool use is not supported",
    "tools are not supported",
    "tool calling is not supported",
)


def proveedor_de(modelo: str) -> str:
    """El proveedor al que se le pide este modelo, a partir de su id.

    Se deduce del prefijo, que es el mismo que usa `agent/llm.py` para elegir
    adaptador: si un día divergieran, el brazo estaría pidiéndole a un proveedor
    y anotando en la ficha de otro.
    """
    for prefijo in ("groq/", "cerebras/", "nvidia/", "mistral/", "openrouter/"):
        if modelo.startswith(prefijo):
            return prefijo.rstrip("/")
    return "gemini" if modelo.startswith("gemini") else "local"


def motivo_permanente(codigo: int | None, texto: str) -> str:
    """`sin_herramientas`, `sin_acceso` o `hay_que_pagar` para un fallo ya permanente.

    Solo se llama con lo que `relevo.tipo_de_agotamiento` clasificó como
    `permanente`; no decide SI es permanente, sino POR QUÉ, que es lo que
    determina si algún día se vuelve a probar.
    """
    bajo = texto.lower()
    if codigo == 402 or "payment" in bajo or "payment_required" in bajo:
        return "hay_que_pagar"
    if any(frase in bajo for frase in _SIN_HERRAMIENTAS):
        return "sin_herramientas"
    return "sin_acceso"


def _revisable(motivo: str, ahora: datetime) -> str | None:
    """Desde cuándo se puede volver a probar. None = nunca."""
    if motivo == "sin_acceso":
        return (ahora + REVISION_SIN_ACCESO).isoformat()
    return None


class Catalogo:
    """La ficha de cada modelo, en un SQLite del volumen.

    ⚠ UN SOLO ARCHIVO PARA TODOS LOS BRAZOS, al contrario que
    `operaciones-<brazo>.db`. Los registros están separados porque cada uno es
    una MUESTRA y mezclarlas la rompe; esto es un hecho del proveedor —el GLM no
    tiene herramientas para nadie— y separarlo por brazo obligaría a redescubrir
    lo mismo en cada uno.

    Se abre en WAL con espera: hasta seis vigías escriben acá, cada uno en su
    proceso. Las escrituras son diminutas y raras (un descarte, o un cambio de
    modelo en el relevo), pero sin `busy_timeout` dos que coincidan dan
    «database is locked» — y este módulo NUNCA debe poder tumbar una vuelta.
    """

    def __init__(self, ruta: str | Path) -> None:
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.ruta, timeout=10.0)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA busy_timeout=10000")
        self._con.executescript(ESQUEMA)
        self._con.commit()

    # ── escritura ───────────────────────────────────────────────────────────

    def descartar(
        self,
        modelo: str,
        *,
        codigo: int | None,
        texto: str,
        ahora: datetime | None = None,
    ) -> str:
        """Anota que este modelo no se puede usar, y por qué. Devuelve el motivo."""
        momento = (ahora or datetime.now(UTC)).astimezone(UTC)
        motivo = motivo_permanente(codigo, texto)
        self._anotar(
            modelo,
            veredicto="descartado",
            motivo=motivo,
            codigo=codigo,
            evidencia=texto[:300],
            decidido_en=momento.isoformat(),
            revisable_desde=_revisable(motivo, momento),
            momento=momento,
            fallo=True,
        )
        return motivo

    def marcar_se_cobra(self, modelo: str, ahora: datetime | None = None) -> None:
        """Un id de OpenRouter sin `:free`: no se llama, y no por un fallo.

        No hay HTTP que lo diga porque la llamada no se hace nunca. Ver el ⚠ de
        `agent/llm.py` sobre el sufijo: el mismo id con y sin él son la misma
        familia y facturas distintas.
        """
        momento = (ahora or datetime.now(UTC)).astimezone(UTC)
        self._anotar(
            modelo,
            veredicto="descartado",
            motivo="se_cobra",
            codigo=None,
            evidencia="sin el sufijo «:free» esta petición se cobra",
            decidido_en=momento.isoformat(),
            revisable_desde=None,
            momento=momento,
        )

    def declarar_sin_herramientas(self, modelo: str, ahora: datetime | None = None) -> None:
        """El catálogo del proveedor dice que este modelo no admite `tools`.

        ⚠ ESTO ES LO QUE EVITA QUE EL PRÓXIMO GLM CUESTE UNA VUELTA. OpenRouter
        publica `supported_parameters` por modelo en su `/api/v1/models`, así que
        «no tiene llamada a herramientas» se puede saber ANTES de pedirle nada —el
        404 del 2026-09-18 costó la vuelta del mediodía del brazo—.

        ⚠ PERO ES METADATO, NO PRUEBA, Y POR ESO SÍ SE REVISA. Un 404 «no
        endpoints found that support tool use» lo dijo el servidor al intentarlo:
        eso no se revisa nunca. Un campo ausente o mal puesto en un catálogo de
        cientos de modelos es otra cosa, y creerle a ciegas «para siempre» tiraría
        un modelo bueno sin que nadie se enterara. Se revisa al mes, como un
        `sin_acceso`: el motivo es el mismo y la evidencia dice de dónde salió.
        """
        momento = (ahora or datetime.now(UTC)).astimezone(UTC)
        self._anotar(
            modelo,
            veredicto="descartado",
            motivo="sin_herramientas",
            codigo=None,
            evidencia="el catálogo del proveedor no declara «tools» en supported_parameters",
            decidido_en=momento.isoformat(),
            revisable_desde=(momento + REVISION_SIN_ACCESO).isoformat(),
            momento=momento,
        )

    def funciono(self, modelo: str, ahora: datetime | None = None) -> None:
        """Este modelo contestó una vuelta de verdad, con herramientas.

        Levanta un descarte anterior: si contestó, el 404 de antes ya no
        describe la realidad. Es la ÚNICA cosa que levanta un descarte, y es la
        correcta porque es la prueba directa.
        """
        momento = (ahora or datetime.now(UTC)).astimezone(UTC)
        self._anotar(
            modelo,
            veredicto="sirve",
            motivo=None,
            codigo=None,
            evidencia=None,
            decidido_en=momento.isoformat(),
            revisable_desde=None,
            momento=momento,
            funciono=momento.isoformat(),
        )

    def visto_en_catalogo(self, modelos: list[str], ahora: datetime | None = None) -> None:
        """Lo que `/v1/models` acaba de listar. NO cambia ningún veredicto.

        Marca `en_catalogo=0` en todo lo del mismo proveedor que ya no salga, que
        es cómo se ve que un id desapareció —el aviso más útil para el brazo de
        OpenRouter, cuyo catálogo gratis cambia sin avisar—.
        """
        momento = (ahora or datetime.now(UTC)).astimezone(UTC).isoformat()
        proveedores = {proveedor_de(m) for m in modelos}
        with self._con:
            for proveedor in proveedores:
                self._con.execute(
                    "UPDATE modelos SET en_catalogo = 0 WHERE proveedor = ?", (proveedor,)
                )
            for modelo in modelos:
                self._con.execute(
                    """INSERT INTO modelos (proveedor, modelo, veredicto, visto_primero,
                                            visto_ultimo, en_catalogo)
                            VALUES (?, ?, 'sin_probar', ?, ?, 1)
                       ON CONFLICT(proveedor, modelo) DO UPDATE SET
                            visto_primero = COALESCE(visto_primero, excluded.visto_primero),
                            visto_ultimo  = excluded.visto_ultimo,
                            en_catalogo   = 1""",
                    (proveedor_de(modelo), modelo, momento, momento),
                )

    def _anotar(
        self,
        modelo: str,
        *,
        veredicto: str,
        motivo: str | None,
        codigo: int | None,
        evidencia: str | None,
        decidido_en: str,
        revisable_desde: str | None,
        momento: datetime,
        funciono: str | None = None,
        fallo: bool = False,
    ) -> None:
        with self._con:
            self._con.execute(
                """INSERT INTO modelos (proveedor, modelo, veredicto, motivo, codigo,
                                        evidencia, decidido_en, revisable_desde,
                                        funciono_en, veces_funciono, veces_fallo)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(proveedor, modelo) DO UPDATE SET
                        veredicto       = excluded.veredicto,
                        motivo          = excluded.motivo,
                        codigo          = excluded.codigo,
                        evidencia       = excluded.evidencia,
                        decidido_en     = excluded.decidido_en,
                        revisable_desde = excluded.revisable_desde,
                        funciono_en     = COALESCE(excluded.funciono_en, funciono_en),
                        veces_funciono  = veces_funciono + excluded.veces_funciono,
                        veces_fallo     = veces_fallo + excluded.veces_fallo""",
                (
                    proveedor_de(modelo),
                    modelo,
                    veredicto,
                    motivo,
                    codigo,
                    evidencia,
                    decidido_en,
                    revisable_desde,
                    funciono,
                    1 if funciono else 0,
                    1 if fallo else 0,
                ),
            )

    # ── lectura ─────────────────────────────────────────────────────────────

    def descartado(self, modelo: str, ahora: datetime | None = None) -> dict[str, Any] | None:
        """La ficha si este modelo está descartado HOY; None si se puede usar.

        Un descarte con `revisable_desde` ya pasado devuelve None: se vuelve a
        probar y el resultado reescribe la ficha.
        """
        fila = self._con.execute(
            "SELECT * FROM modelos WHERE proveedor = ? AND modelo = ?",
            (proveedor_de(modelo), modelo),
        ).fetchone()
        if fila is None or fila["veredicto"] != "descartado":
            return None
        revisable = fila["revisable_desde"]
        if revisable and revisable <= (ahora or datetime.now(UTC)).astimezone(UTC).isoformat():
            return None
        return dict(fila)

    def filtrar(
        self, modelos: list[str], ahora: datetime | None = None
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Parte una lista de modelos en (los usables, las fichas de los descartados).

        Conserva el ORDEN, que en un relevo no es decorativo: el primero de la
        lista es el que se reserva para los cierres de 4h
        (`paper/CRITERIO_HORARIOS.md`).
        """
        usables, fuera = [], []
        for modelo in modelos:
            ficha = self.descartado(modelo, ahora)
            if ficha is None:
                usables.append(modelo)
            else:
                fuera.append(ficha)
        return usables, fuera

    def fichas(self, proveedor: str = "") -> list[dict[str, Any]]:
        """Todo lo que se sabe, por proveedor y modelo. Nunca ordenado por calidad."""
        if proveedor:
            filas = self._con.execute(
                "SELECT * FROM modelos WHERE proveedor = ? ORDER BY modelo", (proveedor,)
            )
        else:
            filas = self._con.execute("SELECT * FROM modelos ORDER BY proveedor, modelo")
        return [dict(f) for f in filas]

    def cerrar(self) -> None:
        self._con.close()
