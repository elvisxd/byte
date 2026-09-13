"""El trace de la sesión: el razonamiento que se mira en vivo.

Lo que se prueba es que sobreviva lo que importa. El trace tiene un tope de
pasos, y el modo en que se llenaba hacía que ese tope borrara justo lo único
que no se puede reconstruir después.
"""

import pytest

from paper.trace import TOPE_PASOS, TraceDeSesion


@pytest.fixture
def trace() -> TraceDeSesion:
    return TraceDeSesion(sesion_id="s1", modelo="granite4.1:8b", simbolo="BTCUSDT")


def test_el_texto_no_expulsa_las_llamadas_a_herramientas(trace: TraceDeSesion) -> None:
    """El modelo emite el texto TOKEN A TOKEN.

    Una respuesta de 500 tokens eran 500 eventos, y con un tope de 200 pasos
    expulsaban todo lo demás. Medido antes del arreglo: una sesión que llamó a
    `mirar_mercado` y abrió una operación terminaba con UN paso de texto y
    ninguna herramienta — o sea, el trace enseñaba justo lo que no hacía falta.
    """
    trace.emit("TOOL_CALL_START", {"toolCallId": "1", "toolCallName": "mirar_mercado"})
    for i in range(TOPE_PASOS * 3):
        trace.emit("TEXT_MESSAGE_CONTENT", {"delta": f"tok{i} "})
    trace.emit("TOOL_CALL_START", {"toolCallId": "2", "toolCallName": "abrir_operacion"})

    pasos = trace.instantanea()["pasos"]

    assert [p["nombre"] for p in pasos if p["tipo"] == "herramienta"] == [
        "mirar_mercado",
        "abrir_operacion",
    ]


def test_el_resultado_de_una_herramienta_viaja(trace: TraceDeSesion) -> None:
    """El grafo emite `{toolCallId, ok, **summary}` y no `content`, así que
    buscar `content` devolvía vacío: el trace enseñaba las llamadas sin lo que
    contestaron, que es la mitad de la historia."""
    trace.emit("TOOL_CALL_RESULT", {"toolCallId": "1", "ok": True, "precio": 77102.99})

    resultado = trace.instantanea()["pasos"][0]

    assert resultado["tipo"] == "resultado"
    assert "77102.99" in resultado["texto"]
    assert "toolCallId" not in resultado["texto"]


def test_el_texto_de_vueltas_distintas_no_se_junta(trace: TraceDeSesion) -> None:
    """Juntar todo el texto en un paso perdería en qué vuelta se dijo cada cosa,
    que es lo que permite leer el trace como una secuencia."""
    trace.vuelta = 1
    trace.emit("TEXT_MESSAGE_CONTENT", {"delta": "primera"})
    trace.vuelta = 2
    trace.emit("TEXT_MESSAGE_CONTENT", {"delta": "segunda"})

    pasos = trace.instantanea()["pasos"]

    assert [p["vuelta"] for p in pasos] == [1, 2]


def test_publicar_sin_panel_no_lanza(trace: TraceDeSesion, monkeypatch: pytest.MonkeyPatch) -> None:
    """Perder la ventana para mirar no es perder el experimento: si publicar el
    trace pudiera abortar una sesión, la comodidad mandaría sobre el trabajo."""
    monkeypatch.delenv("PANEL_URL", raising=False)

    assert trace.publicar() is False


def test_un_panel_caido_no_lanza(trace: TraceDeSesion, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mismo motivo, con el panel configurado pero sin responder."""
    monkeypatch.setenv("PANEL_URL", "http://127.0.0.1:9")

    assert trace.publicar() is False
