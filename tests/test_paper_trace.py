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


def test_el_pensamiento_se_junta_y_tiene_su_propio_recorte(trace: TraceDeSesion) -> None:
    """El pensamiento llega token a token como el texto, pero se recorta más
    largo: es lo que se audita —si el modelo recorre los ejes o repite una
    plantilla— y 400 caracteres cortan antes de la conclusión."""
    from paper.trace import RECORTE, RECORTE_PENSAMIENTO

    trace.vuelta = 1
    for _ in range(RECORTE_PENSAMIENTO // 2):
        trace.emit("THINKING_TEXT_MESSAGE_CONTENT", {"delta": "ab"})
    trace.emit("THINKING_TEXT_MESSAGE_CONTENT", {"delta": "…de más"})

    pasos = trace.instantanea()["pasos"]

    assert len(pasos) == 1
    assert pasos[0]["tipo"] == "pensamiento"
    assert len(pasos[0]["texto"]) > RECORTE, "lo recortó como si fuera texto"
    assert pasos[0]["texto"].startswith("ab" * 10)
    assert "(+" in pasos[0]["texto"], "no recortó nada: crecería sin tope"


def _llamada(trace: TraceDeSesion, cid: str, nombre: str, args: str, resultado: dict) -> None:
    trace.emit("TOOL_CALL_START", {"toolCallId": cid, "toolCallName": nombre})
    trace.emit("TOOL_CALL_ARGS", {"toolCallId": cid, "delta": args})
    trace.emit("TOOL_CALL_RESULT", {"toolCallId": cid, **resultado})


def test_una_llamada_repetida_se_cuenta_en_vez_de_guardarse(trace: TraceDeSesion) -> None:
    """Medido con qwen3:14b: 31 de 44 razones fueron la misma frase, apuntando
    32 veces al mismo nivel ocupado. Ese ×31 ES el hallazgo, así que se
    conserva como contador; las 31 copias eran ruido que expulsaba el resto de
    la sesión del tope de pasos."""
    trace.vuelta = 2
    for i in range(31):
        _llamada(trace, str(i), "predecir", '{"nivel": 76077.62}', {"ok": False, "error": "no"})

    pasos = trace.instantanea()["pasos"]

    assert [p["tipo"] for p in pasos] == ["herramienta", "argumentos", "resultado"]
    assert pasos[0]["nombre"] == "predecir"
    assert pasos[0]["veces"] == 31


def test_llamadas_distintas_no_se_colapsan(trace: TraceDeSesion) -> None:
    """Cambiar el nivel, la herramienta o lo que contestó es otra llamada."""
    trace.vuelta = 1
    _llamada(trace, "1", "predecir", '{"nivel": 76077.62}', {"ok": False})
    _llamada(trace, "2", "predecir", '{"nivel": 79827.4}', {"ok": False})
    _llamada(trace, "3", "dejar_orden", '{"nivel": 79827.4}', {"ok": False})
    _llamada(trace, "4", "dejar_orden", '{"nivel": 79827.4}', {"ok": True})

    pasos = trace.instantanea()["pasos"]

    assert len([p for p in pasos if p["tipo"] == "herramienta"]) == 4
    assert all("veces" not in p for p in pasos)


def test_la_repeticion_no_cruza_de_vuelta(trace: TraceDeSesion) -> None:
    """Repetir en la vuelta 3 lo que ya se repitió en la 2 es otro dato: dice que
    con gráfico nuevo el modelo volvió a lo mismo, y eso se quiere ver."""
    trace.vuelta = 2
    _llamada(trace, "1", "predecir", '{"nivel": 76077.62}', {"ok": False})
    _llamada(trace, "2", "predecir", '{"nivel": 76077.62}', {"ok": False})
    trace.vuelta = 3
    _llamada(trace, "3", "predecir", '{"nivel": 76077.62}', {"ok": False})

    herramientas = [p for p in trace.instantanea()["pasos"] if p["tipo"] == "herramienta"]

    assert [(h["vuelta"], h.get("veces", 1)) for h in herramientas] == [(2, 2), (3, 1)]


def test_el_pensamiento_entre_repeticiones_sobrevive(trace: TraceDeSesion) -> None:
    """Si piensa distinto cada vez, cada pensamiento vale aunque la llamada que
    lo siguió sea la misma: colapsar la llamada no borra lo que pensó antes."""
    trace.vuelta = 1
    trace.emit("THINKING_TEXT_MESSAGE_CONTENT", {"delta": "primero"})
    _llamada(trace, "1", "predecir", "{}", {"ok": False})
    trace.emit("THINKING_TEXT_MESSAGE_CONTENT", {"delta": "segundo, distinto"})
    _llamada(trace, "2", "predecir", "{}", {"ok": False})

    pasos = trace.instantanea()["pasos"]

    assert [p["texto"] for p in pasos if p["tipo"] == "pensamiento"] == [
        "primero",
        "segundo, distinto",
    ]
    assert [p.get("veces") for p in pasos if p["tipo"] == "herramienta"] == [2]
