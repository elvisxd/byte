"""El brazo remoto de la comparación (paper/CRITERIO_COMPARACION.md).

Lo que protege: que cada operación lleve el modelo que la ESCRIBIÓ y no el
primero de la lista; que la traza pueda ir a disco y no al panel; y que `armar`
con una lista de `gemini-…` construya un relevo con el mismo prompt y las
mismas herramientas que el local.
"""

import json
from pathlib import Path
from typing import Any

import pytest

import paper.sesion as sesion
from agent.relevo import Relevo
from api.config import Settings
from paper.registro import Contexto, Registro
from paper.trace import TraceDeSesion


def _ctx(precio: float) -> Contexto:
    return Contexto(
        precio=precio,
        timestamp="2026-09-15T12:00:00+00:00",
        dia_semana=1,
        hora_utc=12,
        extra={"indicadores": {"atr": 200.0}},
    )


def test_el_registro_sella_el_modelo_que_contesta_ahora(tmp_path: Path) -> None:
    actual = {"nombre": "gemini-3.8-flash"}
    registro = Registro(tmp_path / "r.db", modelo=lambda: actual["nombre"])

    primera = registro.predecir(
        simbolo="BTCUSDT",
        contexto=_ctx(79000),
        nivel=80000,
        hacia="arriba",
        probabilidad=0.4,
        razonamiento="a",
        horas_vigencia=6,
        temporalidad="1h",
    )
    actual["nombre"] = "gemini-3.5-flash-lite"
    segunda = registro.predecir(
        simbolo="BTCUSDT",
        contexto=_ctx(79000),
        nivel=70000,
        hacia="abajo",
        probabilidad=0.4,
        razonamiento="b",
        horas_vigencia=6,
        temporalidad="1h",
    )

    modelos = {p["id"]: p["modelo"] for p in registro.predicciones_vivas()}
    assert modelos[primera] == "gemini-3.8-flash"
    assert modelos[segunda] == "gemini-3.5-flash-lite"


def test_la_traza_a_archivo_no_toca_el_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PANEL_URL", "http://panel.invalido")
    llamadas: list[Any] = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: llamadas.append(a))
    archivo = tmp_path / "trazas" / "gemini.json"
    trace = TraceDeSesion(
        sesion_id="g1", modelo=lambda: "gemini-3.7-flash", simbolo="BTCUSDT", archivo=str(archivo)
    )
    trace.emit("TEXT_MESSAGE_CONTENT", {"delta": "hola"})

    assert trace.publicar(viva=True) is True
    assert llamadas == []
    guardado = json.loads(archivo.read_text())
    assert guardado["modelo"] == "gemini-3.7-flash"
    assert guardado["pasos"][0]["texto"] == "hola"


def test_armar_con_gemini_construye_un_relevo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    construidos: list[tuple[str, bool]] = []

    class _Falso:
        def bind_tools(self, _e: Any) -> "_Falso":
            return self

    def build_llm_falso(ajustes: Settings, modelo: str = "", **kw: Any) -> _Falso:
        construidos.append((modelo, kw.get("reasoning", False)))
        return _Falso()

    monkeypatch.setattr(sesion, "build_llm", build_llm_falso)
    capturado: dict[str, Any] = {}
    grafo_original = sesion.build_graph

    def build_graph_espia(llm: Any, herramientas: Any, **kw: Any) -> Any:
        capturado["llm"] = llm
        capturado["kw"] = kw
        return grafo_original(llm, herramientas, **kw)

    monkeypatch.setattr(sesion, "build_graph", build_graph_espia)
    ajustes = Settings(GEMINI_API_KEY="clave", BYTE_MAX_ITERATIONS=6)

    _, _, etiqueta = sesion.armar(
        ajustes, str(tmp_path / "g.db"), ["gemini-3.8-flash", "gemini-3.7-flash"]
    )

    assert construidos == [("gemini-3.8-flash", True), ("gemini-3.7-flash", True)]
    assert isinstance(capturado["llm"], Relevo)
    assert capturado["llm"].nombres == ["gemini-3.8-flash", "gemini-3.7-flash"]
    # Lo mismo que el local: ni más iteraciones ni más contexto.
    assert capturado["kw"]["max_iterations"] == ajustes.max_iterations
    assert capturado["kw"]["num_ctx"] == ajustes.paper_num_ctx
    assert callable(etiqueta) and etiqueta() == "gemini-3.8-flash"


def test_no_se_mezclan_locales_y_remotos(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mezcla"):
        sesion.armar(
            Settings(GEMINI_API_KEY="k"), str(tmp_path / "x.db"), ["gemini-3.8-flash", "qwen3:14b"]
        )
