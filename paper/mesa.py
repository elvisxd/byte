"""La mesa de analistas, fase 1: en sombra. Ver paper/CRITERIO_MESA_ANALISTAS.md,
que se escribió antes que esto y en su propio commit.

═══ QUÉ HACE ═══

Tras cada cierre de 4h dentro de la ventana del vigía, con `MESA_ESPERA_MIN`
de retraso, le hace LA MISMA pregunta a un analista de cada familia:

    marco 1h, plazo 24 h: ¿toca arriba = precio + 1 ATR? ¿y abajo = precio − 1 ATR?

Cada uno contesta con sus dos probabilidades y un veredicto (alcista, bajista
o neutral). Se guarda en `mesa.db`, se manda un aviso por Telegram con la mesa
entera, y el código resuelve cada pregunta contra las velas de 15m cuando el
precio toca o vence el plazo.

⚠ EL TRADER NO VE NADA DE ESTO. Es lo que permite correrla a mitad de la
muestra v5: ningún brazo lee `mesa.db` ni recibe la mesa en su mensaje.

⚠ NUNCA TUMBA A LOS BRAZOS. Corre en su propio proceso, fuera de la lista que
`arrancar.sh` vigila con `wait -n`, y cualquier fallo —una clave agotada, el
mercado caído, Telegram— deja la ronda incompleta y sigue.

Uso (en el contenedor lo lanza arrancar.sh):

    python -m paper.mesa --db $DATOS/mesa.db \\
        --familia gemini=gemini-3.8-flash,gemini-3.7-flash \\
        --familia groq=groq/openai/gpt-oss-120b
    python -m paper.mesa --db $DATOS/mesa.db --informe
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from paper.analista import _texto_de, interpretar_probabilidades
from paper.prompt import ROL_ANALISTA

# La pregunta: el marco por defecto de las predicciones y su plazo
# (`Registro.PLAZO_POR_MARCO["1h"]`), a 1 ATR, que es una de las dos distancias
# con tasa base en el mapa.
MARCO = "1h"
PLAZO_H = 24.0
DISTANCIA_ATR = 1.0
PERIODO_ATR = 14
CUATRO_HORAS_S = 4 * 3600
# Cada cuánto mira el reloj. La ronda no necesita precisión de segundos.
CADA_S = 300
# La puerta del criterio: ni Brier ni acierto antes de esto, por familia.
PUERTA = 50
TOPE_TEXTO = 700
# Fase 1b (CRITERIO_MESA_ANALISTAS.md): las dos variantes de cada familia.
VARIANTE_MAPA = "mapa"
VARIANTE_EXTERNO = "mapa+externo"
# La segunda tanda espera a la primera: Groq cuenta 8.000 tokens POR MINUTO y
# las dos preguntas llevan el mapa entero.
ESPERA_TANDA_S = 65.0
# La vara (CRITERIO_MESA_ANALISTAS.md, «La tasa base, como un analista más»):
# la tasa base que el mapa le dio a la mesa, guardada como una familia más para
# medir a cada analista contra el número que ya tenía delante. No es un
# analista: no entra en el consenso, la discrepancia ni el «contestaron».
FAMILIA_BASE = "tasa base"

_VEREDICTO = re.compile(
    r"VEREDICTO:\s*(alcista|bajista|neutral)\s*(?:[—–-]+\s*(.*))?", re.IGNORECASE
)

INSTRUCCION_MESA = """Leé el mapa de abajo. Una sola pregunta, la misma que les llega a los demás
analistas de la mesa:

marco {marco}, plazo {plazo:g} h desde ahora (precio {precio:,.2f}):
  · ¿el precio TOCA {arriba:,.2f} (arriba, +{dist:g} ATR de {marco}) antes de vencer?
  · ¿el precio TOCA {abajo:,.2f} (abajo, −{dist:g} ATR de {marco}) antes de vencer?

Partí de la tasa base que el mapa da para {marco} a {dist:g} ATR y ajustala por lo
que veas. En menos de 80 palabras: el régimen, y qué te hace inclinarte.

Terminá SIEMPRE con estas dos líneas exactas, con tus números:
PROBABILIDADES: arriba <probabilidad>% | abajo <probabilidad>%
VEREDICTO: <alcista|bajista|neutral> — <por qué, en menos de 20 palabras>"""


_TASA_BASE = re.compile(r"un nivel a (\d+) ATR se tocó dentro de \d+ velas el (\d+)%")


def tasa_base_del_mapa(mapa: str, marco: str = MARCO, dist: float = DISTANCIA_ATR) -> float | None:
    """La tasa base del bloque de `marco` a `dist` ATR, tal como la leyó la mesa.

    Del texto del mapa y no recalculada: la vara es el número que el analista
    tenía delante (`tools/paper.py`, `_tasa_base`). Sin bloque o sin línea, None
    y la ronda sigue sin vara."""
    cabeza = f"── {marco} ──"
    i = mapa.find(cabeza)
    if i < 0:
        return None
    fin = mapa.find("\n── ", i + len(cabeza))
    bloque = mapa[i : fin if fin >= 0 else len(mapa)]
    for linea in bloque.splitlines():
        if "tasa base" not in linea:
            continue
        m = _TASA_BASE.search(linea)
        if m and float(m.group(1)) == dist:
            return int(m.group(2)) / 100
    return None


# ── la pregunta ─────────────────────────────────────────────────────────────


def atr(velas: list[dict[str, Any]], periodo: int = PERIODO_ATR) -> float | None:
    """ATR simple (media del rango verdadero) de las últimas `periodo` velas cerradas."""
    if len(velas) < periodo + 2:
        return None
    cerradas = velas[:-1]  # la última está en curso
    rangos = []
    for prev, v in zip(cerradas[-periodo - 1 : -1], cerradas[-periodo:], strict=True):
        rangos.append(
            max(v["high"] - v["low"], abs(v["high"] - prev["close"]), abs(v["low"] - prev["close"]))
        )
    return sum(rangos) / len(rangos)


@dataclass(frozen=True)
class Pregunta:
    cierre_4h: int  # epoch s del cierre de 4h que la motivó
    hecha_en: datetime
    precio: float
    atr: float
    arriba: float
    abajo: float
    vence_en: datetime


def pregunta(cierre_4h: int, ahora: datetime, velas_1h: list[dict[str, Any]]) -> Pregunta | None:
    """La pregunta de la ronda, fijada por el código: nadie de la mesa elige los niveles."""
    a = atr(velas_1h)
    if not a or not velas_1h:
        return None
    precio = float(velas_1h[-1]["close"])
    return Pregunta(
        cierre_4h=cierre_4h,
        hecha_en=ahora,
        precio=precio,
        atr=a,
        arriba=round(precio + DISTANCIA_ATR * a, 2),
        abajo=round(precio - DISTANCIA_ATR * a, 2),
        vence_en=ahora + timedelta(hours=PLAZO_H),
    )


def cierre_pendiente(
    ahora: datetime,
    hechas: set[int],
    espera_min: float,
    en_ventana: Callable[[datetime], bool],
) -> int | None:
    """El cierre de 4h que toca atender ahora, o None.

    El último cierre, si ya pasó la espera, cayó dentro de la ventana (en hora
    LOCAL, la del contenedor) y no tiene ronda. Solo el último: un cierre que se
    perdió —el servicio estaba caído— no se contesta horas tarde con un mapa
    que ya no es el suyo.
    """
    t = int(ahora.timestamp())
    cierre = t - t % CUATRO_HORAS_S
    if cierre in hechas:
        return None
    if t - cierre < espera_min * 60:
        return None
    # Tampoco si ya casi llega el siguiente: la espera más una hora es el tope.
    if t - cierre > espera_min * 60 + 3600:
        return None
    if not en_ventana(datetime.fromtimestamp(cierre).astimezone()):
        return None
    return cierre


# ── las respuestas ──────────────────────────────────────────────────────────


@dataclass
class Respuesta:
    familia: str
    modelo: str = ""
    p_arriba: float | None = None
    p_abajo: float | None = None
    veredicto: str | None = None
    porque: str = ""
    texto: str = ""
    fallo: str = ""
    variante: str = VARIANTE_MAPA


def interpretar(familia: str, modelo: str, texto: str) -> Respuesta:
    r = Respuesta(familia=familia, modelo=modelo, texto=texto.strip()[:TOPE_TEXTO])
    par = interpretar_probabilidades(texto)
    if par is not None and all(0.0 <= p <= 1.0 for p in par):
        r.p_arriba, r.p_abajo = par
    m = _VEREDICTO.search(texto)
    if m:
        r.veredicto = m.group(1).lower()
        r.porque = (m.group(2) or "").strip()[:160]
    if r.p_arriba is None:
        r.fallo = "sin la línea PROBABILIDADES"
    return r


async def preguntar(familia: str, llm: Any, p: Pregunta, mapa: str, externo: str = "") -> Respuesta:
    """Una familia, una muestra. Un fallo es una respuesta vacía, nunca una excepción.

    Con `externo`, el bloque de contexto externo va DESPUÉS del mapa y es lo
    único que cambia entre las dos variantes (fase 1b del criterio)."""
    variante = VARIANTE_EXTERNO if externo else VARIANTE_MAPA
    contenido = INSTRUCCION_MESA.format(
        marco=MARCO,
        plazo=PLAZO_H,
        precio=p.precio,
        arriba=p.arriba,
        abajo=p.abajo,
        dist=DISTANCIA_ATR,
    )
    try:
        respuesta = await llm.ainvoke(
            [
                SystemMessage(content=ROL_ANALISTA),
                HumanMessage(
                    content=f"{contenido}\n\n{mapa}" + (f"\n\n{externo}" if externo else "")
                ),
            ]
        )
    except Exception as exc:  # noqa: BLE001 — una familia menos, no una ronda menos
        return Respuesta(
            familia=familia,
            modelo=str(getattr(llm, "actual", "") or ""),
            fallo=str(exc)[:160],
            variante=variante,
        )
    r = interpretar(familia, str(getattr(llm, "actual", "") or ""), _texto_de(respuesta))
    r.variante = variante
    return r


def consenso(respuestas: list[Respuesta]) -> tuple[float, float, float] | None:
    """(media arriba, media abajo, discrepancia) de las que contestaron.

    La discrepancia es la mayor distancia de una familia a la media, en puntos,
    tomando el peor de los dos niveles: «discrepan ±12» quiere decir que alguna
    familia está 12 puntos lejos del resto en algún nivel.
    """
    buenas = [r for r in respuestas if r.p_arriba is not None and r.p_abajo is not None]
    if not buenas:
        return None
    ma = sum(r.p_arriba for r in buenas) / len(buenas)  # type: ignore[misc]
    mb = sum(r.p_abajo for r in buenas) / len(buenas)  # type: ignore[misc]
    dis = max(max(abs(r.p_arriba - ma), abs(r.p_abajo - mb)) for r in buenas)  # type: ignore[operator]
    return ma, mb, dis


# ── el aviso ────────────────────────────────────────────────────────────────

_ICONO = {"alcista": "🟢", "bajista": "🔴", "neutral": "⚪"}


def _pct(p: float | None) -> str:
    return "—" if p is None else f"{round(p * 100)}%"


def mensaje(
    p: Pregunta, respuestas: list[Respuesta], externo: str = "", base: float | None = None
) -> str:
    """El aviso de Telegram de una ronda. Texto plano: la razón la escribe un modelo.

    La línea de cada familia es la de la variante `mapa` (la serie de la fase
    1); debajo, en una línea corta, lo que dijo CON el contexto externo. La
    tasa base va aparte, al final: es la vara, no un analista."""
    respuestas = [r for r in respuestas if r.familia != FAMILIA_BASE]
    con_externo = {r.familia: r for r in respuestas if r.variante == VARIANTE_EXTERNO}
    respuestas = [r for r in respuestas if r.variante == VARIANTE_MAPA]
    hora = datetime.fromtimestamp(p.cierre_4h).astimezone().strftime("%H:%M")
    lineas = [
        f"🧑‍💼 Mesa de analistas · BTC tras el cierre de 4h de las {hora}",
        f"Precio {p.precio:,.0f} · ¿toca en {PLAZO_H:g} h? ↑ {p.arriba:,.0f} · ↓ {p.abajo:,.0f} "
        f"(±{DISTANCIA_ATR:g} ATR {MARCO} = {p.atr:,.0f})",
        "",
    ]
    for r in respuestas:
        modelo = r.modelo.split("/")[-1] if r.modelo else "?"
        if r.p_arriba is None:
            lineas.append(f"• {r.familia} ({modelo}): sin respuesta — {r.fallo[:90]}")
            continue
        icono = _ICONO.get(r.veredicto or "", "·")
        cabeza = (
            f"• {icono} {r.familia} ({modelo}): {r.veredicto or 'sin veredicto'}"
            f" · ↑ {_pct(r.p_arriba)} · ↓ {_pct(r.p_abajo)}"
        )
        lineas.append(cabeza + (f"\n   {r.porque}" if r.porque else ""))
        b = con_externo.get(r.familia)
        if b is not None:
            lineas.append(
                f"   con contexto: {b.veredicto or 'sin veredicto'}"
                f" · ↑ {_pct(b.p_arriba)} · ↓ {_pct(b.p_abajo)}"
                if b.p_arriba is not None
                else f"   con contexto: sin respuesta — {b.fallo[:60]}"
            )
    c = consenso(respuestas)
    lineas.append("")
    if c:
        votos = [r.veredicto for r in respuestas if r.veredicto]
        conteo = " · ".join(
            f"{v} {votos.count(v)}" for v in ("alcista", "bajista", "neutral") if votos.count(v)
        )
        lineas.append(
            f"Consenso: ↑ {_pct(c[0])} · ↓ {_pct(c[1])} · discrepan ±{round(c[2] * 100)} pts"
        )
        if conteo:
            lineas.append(f"Veredictos: {conteo}")
    else:
        lineas.append("Nadie contestó esta ronda.")
    ce = consenso(list(con_externo.values()))
    if ce:
        lineas.append(
            f"Con contexto: ↑ {_pct(ce[0])} · ↓ {_pct(ce[1])} · discrepan ±{round(ce[2] * 100)} pts"
        )
    if base is not None:
        lineas.append(f"Tasa base del mapa (la vara): ↑ {_pct(base)} · ↓ {_pct(base)}")
    # Del bloque, lo que más se mira: la macro y el calendario.
    for linea in externo.splitlines():
        if linea.startswith(("Macro 24h:", "Calendario:")):
            lineas.append(linea[:220])
    lineas.append("(en sombra: el trader no ve la mesa)")
    return "\n".join(lineas)[:4000]


# ── el registro ─────────────────────────────────────────────────────────────


class Mesa:
    """`mesa.db`: las rondas, las respuestas y cómo se resolvió cada pregunta."""

    def __init__(self, ruta: str) -> None:
        self._con = sqlite3.connect(ruta)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(
            """
            CREATE TABLE IF NOT EXISTS rondas (
                id          INTEGER PRIMARY KEY,
                cierre_4h   INTEGER NOT NULL UNIQUE,
                hecha_en    TEXT    NOT NULL,
                precio      REAL    NOT NULL,
                atr         REAL    NOT NULL,
                arriba      REAL    NOT NULL,
                abajo       REAL    NOT NULL,
                vence_en    TEXT    NOT NULL,
                toco_arriba INTEGER,            -- 1 tocó; 0 venció sin tocar; NULL viva
                toco_abajo  INTEGER,
                resuelta_en TEXT
            );
            CREATE TABLE IF NOT EXISTS respuestas (
                id        INTEGER PRIMARY KEY,
                ronda_id  INTEGER NOT NULL REFERENCES rondas(id),
                familia   TEXT    NOT NULL,
                modelo    TEXT,
                p_arriba  REAL,
                p_abajo   REAL,
                veredicto TEXT,
                porque    TEXT,
                texto     TEXT,
                fallo     TEXT
            );
            CREATE TABLE IF NOT EXISTS contextos (
                ronda_id  INTEGER PRIMARY KEY REFERENCES rondas(id),
                bloque    TEXT    NOT NULL,
                datos     TEXT,              -- JSON: los números con que se armó el bloque
                fallos    TEXT               -- JSON: qué fuente no respondió
            );
            """
        )
        # Fase 1b: la variante de cada respuesta. Las de la fase 1 son `mapa`.
        columnas = {f[1] for f in self._con.execute("PRAGMA table_info(respuestas)")}
        if "variante" not in columnas:
            self._con.execute(
                "ALTER TABLE respuestas ADD COLUMN variante TEXT NOT NULL"
                f" DEFAULT '{VARIANTE_MAPA}'"
            )
        self._con.commit()

    def hechas(self) -> set[int]:
        return {int(f[0]) for f in self._con.execute("SELECT cierre_4h FROM rondas")}

    def guardar(self, p: Pregunta, respuestas: list[Respuesta], contexto: Any = None) -> int:
        cur = self._con.execute(
            """INSERT INTO rondas (cierre_4h, hecha_en, precio, atr, arriba, abajo, vence_en)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                p.cierre_4h,
                p.hecha_en.isoformat(),
                p.precio,
                p.atr,
                p.arriba,
                p.abajo,
                p.vence_en.isoformat(),
            ),
        )
        ronda = int(cur.lastrowid or 0)
        self._con.executemany(
            """INSERT INTO respuestas
                 (ronda_id, familia, modelo, p_arriba, p_abajo, veredicto, porque, texto, fallo,
                  variante)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    ronda,
                    r.familia,
                    r.modelo,
                    r.p_arriba,
                    r.p_abajo,
                    r.veredicto,
                    r.porque,
                    r.texto,
                    r.fallo,
                    r.variante,
                )
                for r in respuestas
            ],
        )
        if contexto is not None:
            self._con.execute(
                "INSERT INTO contextos (ronda_id, bloque, datos, fallos) VALUES (?, ?, ?, ?)",
                (
                    ronda,
                    contexto.bloque,
                    json.dumps(contexto.datos, ensure_ascii=False, default=str),
                    json.dumps(contexto.fallos, ensure_ascii=False),
                ),
            )
        self._con.commit()
        return ronda

    def resolver(self, velas_15m: list[dict[str, Any]], ahora: datetime) -> int:
        """Cierra lo que tocó o venció, por código, con `high`/`low` y solo las
        velas POSTERIORES a la pregunta —mismo criterio que
        `Registro.resolver_predicciones`—. Devuelve cuántas rondas se cerraron."""
        cerradas = 0
        for r in self._con.execute("SELECT * FROM rondas WHERE resuelta_en IS NULL").fetchall():
            hecha = datetime.fromisoformat(r["hecha_en"])
            vence = datetime.fromisoformat(r["vence_en"])
            arriba, abajo = r["toco_arriba"] == 1, r["toco_abajo"] == 1
            for v in velas_15m:
                momento = datetime.fromtimestamp(v["time"], UTC)
                if momento < hecha:
                    continue
                if momento > vence:
                    break
                arriba = arriba or v["high"] >= r["arriba"]
                abajo = abajo or v["low"] <= r["abajo"]
            final = (arriba and abajo) or ahora > vence
            self._con.execute(
                "UPDATE rondas SET toco_arriba=?, toco_abajo=?, resuelta_en=? WHERE id=?",
                (
                    1 if arriba else (0 if final else None),
                    1 if abajo else (0 if final else None),
                    ahora.isoformat() if final else None,
                    r["id"],
                ),
            )
            cerradas += int(final)
        self._con.commit()
        return cerradas

    def informe(self) -> str:
        """Por familia y variante: contestadas, resueltas y cuánto se aparta del
        resto. El Brier solo desde la puerta de 50 resueltas, por familia Y por
        variante (el criterio): antes, la diferencia entre variantes es ruido.

        Desde la puerta, cada familia lleva también su skill contra la tasa base
        en las MISMAS rondas y variante: 1 − Brier(familia) / Brier(tasa base)."""
        filas = self._con.execute(
            """SELECT s.familia, s.variante, s.p_arriba, s.p_abajo, r.id AS ronda,
                      r.toco_arriba, r.toco_abajo, r.resuelta_en
               FROM respuestas s JOIN rondas r ON r.id = s.ronda_id"""
        ).fetchall()
        rondas = self._con.execute("SELECT COUNT(*), COUNT(resuelta_en) FROM rondas").fetchone()
        salida = [
            f"Mesa de analistas: {rondas[0]} rondas, {rondas[1]} resueltas"
            f" (puerta del Brier: {PUERTA} por familia y variante)"
        ]
        # La media de cada ronda y variante, para la discrepancia contra el resto.
        # La tasa base no es un analista: fuera de la media.
        por_ronda: dict[tuple[int, str], list[Any]] = {}
        for f in filas:
            if f["p_arriba"] is not None and f["familia"] != FAMILIA_BASE:
                por_ronda.setdefault((f["ronda"], f["variante"]), []).append(f)
        vara = {
            (f["ronda"], f["variante"]): f
            for f in filas
            if f["familia"] == FAMILIA_BASE and f["p_arriba"] is not None
        }
        medias = {
            k: (sum(x["p_arriba"] for x in fs) / len(fs), sum(x["p_abajo"] for x in fs) / len(fs))
            for k, fs in por_ronda.items()
        }
        for familia, variante in sorted({(f["familia"], f["variante"]) for f in filas}):
            propias = [f for f in filas if f["familia"] == familia and f["variante"] == variante]
            contestadas = [f for f in propias if f["p_arriba"] is not None]
            resueltas = [f for f in contestadas if f["resuelta_en"]]
            dis = [
                (
                    abs(f["p_arriba"] - medias[(f["ronda"], variante)][0])
                    + abs(f["p_abajo"] - medias[(f["ronda"], variante)][1])
                )
                / 2
                for f in contestadas
                if len(por_ronda.get((f["ronda"], variante), [])) > 1
            ]
            es_vara = familia == FAMILIA_BASE
            aparte = round(100 * sum(dis) / len(dis)) if dis else "—"
            linea = (
                f"  {familia} [{variante}]: contestó {len(contestadas)}/{len(propias)}"
                f" · resueltas {len(resueltas)}"
                + ("" if es_vara else f" · se aparta del resto {aparte} pts de media")
            )
            if len(resueltas) >= PUERTA:
                linea += f" · Brier {_brier(resueltas):.4f}"
                pares = [f for f in resueltas if (f["ronda"], variante) in vara]
                if not es_vara and len(pares) >= PUERTA:
                    de_la_vara = _brier([vara[(f["ronda"], variante)] for f in pares])
                    if de_la_vara > 0:
                        skill = 1 - _brier(pares) / de_la_vara
                        linea += f" · vs tasa base {skill:+.3f} en {len(pares)}"
            salida.append(linea)
        return "\n".join(salida)


def _brier(filas: list[Any]) -> float:
    """Brier medio de filas resueltas, los dos niveles de cada pregunta."""
    return sum(
        (f["p_arriba"] - f["toco_arriba"]) ** 2 + (f["p_abajo"] - f["toco_abajo"]) ** 2
        for f in filas
    ) / (2 * len(filas))


# ── el bucle ────────────────────────────────────────────────────────────────


def familias_de(args: list[str]) -> dict[str, list[str]]:
    """`gemini=a,b` → {"gemini": ["a", "b"]}. Sin `:free`, OpenRouter no entra
    (se cobra; mismo criterio que `paper/sesion.py`)."""
    out: dict[str, list[str]] = {}
    for a in args:
        nombre, _, lista = a.partition("=")
        modelos = [m.strip() for m in lista.split(",") if m.strip()]
        modelos = [
            m for m in modelos if not (m.startswith("openrouter/") and not m.endswith(":free"))
        ]
        if nombre.strip() and modelos:
            out[nombre.strip()] = modelos
    return out


async def ronda(
    cierre: int,
    mesa: Mesa,
    llms: dict[str, Any],
    velas: Callable[[str, int], list[dict[str, Any]]],
    mapa: Callable[[], str],
    avisar: Callable[[str], bool],
    ahora: datetime | None = None,
    contexto: Callable[[], Any] | None = None,
    dormir: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> str | None:
    """Una ronda completa. Devuelve el aviso, o None si no se pudo preguntar.

    Con `contexto` (fase 1b), dos tandas: primero todas las familias con el
    mapa solo, y tras `ESPERA_TANDA_S` todas con el mapa y el bloque externo.
    El bloque se reúne MIENTRAS contesta la primera tanda.
    """
    ahora = ahora or datetime.now(UTC)
    p = pregunta(cierre, ahora, velas("1h", 60))
    texto_mapa = mapa()
    if p is None or not texto_mapa:
        print("[mesa] sin mercado para la pregunta: la ronda se salta", flush=True)
        return None
    tarea_ctx = asyncio.create_task(asyncio.to_thread(contexto)) if contexto else None
    respuestas = list(
        await asyncio.gather(*(preguntar(f, llm, p, texto_mapa) for f, llm in llms.items()))
    )
    ctx = None
    if tarea_ctx is not None:
        try:
            ctx = await tarea_ctx
        except Exception as exc:  # noqa: BLE001 — sin contexto, la ronda es la de la fase 1
            print(f"[mesa] sin contexto externo: {str(exc)[:120]}", flush=True)
    if ctx is not None and ctx.bloque:
        await dormir(ESPERA_TANDA_S)
        respuestas += list(
            await asyncio.gather(
                *(preguntar(f, llm, p, texto_mapa, ctx.bloque) for f, llm in llms.items())
            )
        )
    base = tasa_base_del_mapa(texto_mapa)
    varas = (
        [
            Respuesta(FAMILIA_BASE, "mapa", base, base, variante=v)
            for v in (VARIANTE_MAPA, VARIANTE_EXTERNO)
            if any(r.variante == v for r in respuestas)
        ]
        if base is not None
        else []
    )
    mesa.guardar(p, respuestas + varas, ctx)
    aviso = mensaje(p, respuestas, ctx.bloque if ctx is not None else "", base)
    enviado = avisar(aviso)
    por_variante = " · ".join(
        f"{v} {sum(r.p_arriba is not None for r in respuestas if r.variante == v)}"
        f"/{sum(r.variante == v for r in respuestas)}"
        for v in (VARIANTE_MAPA, VARIANTE_EXTERNO)
        if any(r.variante == v for r in respuestas)
    )
    faltan = f" · fuentes sin dato: {', '.join(sorted(ctx.fallos))}" if ctx and ctx.fallos else ""
    print(
        f"[mesa] ronda del cierre {cierre}: {por_variante} contestaron"
        f" · tasa base {_pct(base) if base is not None else 'sin dato'}"
        f" · telegram {'enviado' if enviado else 'NO enviado'}{faltan}",
        flush=True,
    )
    return aviso


async def vigilar(
    mesa: Mesa,
    llms: dict[str, Any],
    espera_min: float,
    en_ventana: Callable[[datetime], bool],
    velas: Callable[[str, int], list[dict[str, Any]]],
    mapa: Callable[[], str],
    avisar: Callable[[str], bool],
    parar: asyncio.Event,
    dormir: Callable[[float], Awaitable[Any]] | None = None,
    contexto: Callable[[], Any] | None = None,
) -> None:
    while not parar.is_set():
        try:
            try:
                mesa.resolver(velas("15m", 200), datetime.now(UTC))
            except Exception as exc:  # noqa: BLE001 — sin velas se resuelve en la próxima
                print(f"[mesa] no se pudo resolver: {str(exc)[:120]}", flush=True)
            cierre = cierre_pendiente(datetime.now(UTC), mesa.hechas(), espera_min, en_ventana)
            if cierre is not None:
                await ronda(cierre, mesa, llms, velas, mapa, avisar, contexto=contexto)
        except Exception as exc:  # noqa: BLE001 — la mesa nunca se cae por una ronda
            print(f"[mesa] ronda fallida: {str(exc)[:160]}", flush=True)
        if dormir is not None:
            await dormir(CADA_S)
            continue
        try:
            await asyncio.wait_for(parar.wait(), timeout=CADA_S)
        except TimeoutError:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(
        description="La mesa de analistas en sombra (paper/CRITERIO_MESA_ANALISTAS.md)."
    )
    ap.add_argument("--db", required=True)
    ap.add_argument(
        "--familia", action="append", default=[], help="nombre=modelo1,modelo2 (una por familia)"
    )
    ap.add_argument("--informe", action="store_true", help="imprime el parte y sale")
    args = ap.parse_args()
    mesa = Mesa(args.db)
    if args.informe:
        print(mesa.informe())
        return

    # Import tardío: el informe no necesita claves ni red.
    from agent.llm import build_llm
    from agent.relevo import Relevo
    from api.config import get_settings
    from paper.mercado import velas as velas_del_mercado
    from paper.sesion import SIMBOLO
    from paper.vigia import avisar_por_telegram, en_ventana
    from tools.paper import _mapa

    ajustes = get_settings()
    familias = familias_de(args.familia)
    if not familias:
        print("[mesa] ✗ sin familias: nada que preguntar", flush=True)
        return
    llms: dict[str, Any] = {}
    for nombre, modelos in familias.items():
        try:
            llms[nombre] = Relevo([(m, build_llm(ajustes, m)) for m in modelos], espera_s=5.0)
        except Exception as exc:  # noqa: BLE001 — una familia sin clave no apaga la mesa
            print(f"[mesa] ⚠ {nombre} fuera de la mesa: {str(exc)[:120]}", flush=True)
    if not llms:
        print("[mesa] ✗ ninguna familia se pudo armar", flush=True)
        return
    espera = float(os.environ.get("MESA_ESPERA_MIN", "50"))
    # Fase 1b: el A/B con el contexto externo. `MESA_CONTEXTO=0` vuelve a la fase 1.
    con_contexto = os.environ.get("MESA_CONTEXTO", "1") != "0"
    from paper.contexto_externo import reunir as reunir_contexto

    print(
        f"[mesa] en sombra · familias: {', '.join(llms)} · {espera:g} min tras cada cierre de 4h"
        f" · contexto externo {'en A/B' if con_contexto else 'apagado'}",
        flush=True,
    )

    def velas(marco: str, cuantas: int) -> list[dict[str, Any]]:
        return velas_del_mercado(SIMBOLO, marco, cuantas)["velas"]

    def mapa() -> str:
        r = _mapa(SIMBOLO, ajustes.paper_max_tool_result_chars)
        return r.content if r.ok else ""

    parar = asyncio.Event()

    async def correr() -> None:
        bucle = asyncio.get_running_loop()
        for s in (signal.SIGTERM, signal.SIGINT):
            bucle.add_signal_handler(s, parar.set)
        await vigilar(
            mesa,
            llms,
            espera,
            en_ventana,
            velas,
            mapa,
            avisar_por_telegram,
            parar,
            contexto=reunir_contexto if con_contexto else None,
        )

    asyncio.run(correr())


if __name__ == "__main__":
    main()
