"""La foto del registro que viaja al panel.

Lo que se prueba acá no es que los campos se copien —eso lo rompería cualquier
cambio y no diría nada— sino las tres cosas que hacen que el panel mienta si
salen mal: que el sello roto viaje, que el orden no invite al sobreajuste, y
que una operación abierta no se cuele entre las cerradas.
"""

import json
from pathlib import Path

import pytest

from paper.publicar import instantanea, publicar
from paper.registro import Contexto, Registro


@pytest.fixture
def registro(tmp_path: Path) -> Registro:
    return Registro(tmp_path / "ops.db")


def _contexto(precio: float = 100.0) -> Contexto:
    return Contexto(
        precio=precio,
        timestamp="2026-09-13T10:00:00Z",
        dia_semana=0,
        hora_utc=10,
        ancho_rango_pct=0.8,
        regimen_btc="ascending",
    )


def _operacion(
    registro: Registro, eje: str = "range-sweep", razon: str = "Barrido del piso."
) -> int:
    return registro.abrir(
        eje=eje,
        simbolo="AAVEUSDT",
        direccion="long",
        contexto=_contexto(),
        razon=razon,
        stop_loss=99.0,
        take_profit=104.0,
    )


def test_una_operacion_abierta_no_aparece_entre_las_cerradas(registro: Registro) -> None:
    """Contarla como cerrada le daría al eje un R que todavía no tiene."""
    _operacion(registro)
    cerrada = _operacion(registro)
    registro.cerrar(cerrada, precio_salida=103.0, motivo="objetivo")

    foto = instantanea(registro)

    assert [o["id"] for o in foto["abiertas"]] == [1]
    assert [o["id"] for o in foto["cerradas"]] == [2]
    assert foto["cerradas"][0]["rMultiplo"] == pytest.approx(3.0)


def test_el_sello_roto_viaja_en_la_foto(registro: Registro) -> None:
    """Es el dato que decide si el resto vale algo. Si el panel no lo recibe,
    enseña un historial adulterado como si fuera bueno."""
    oid = _operacion(registro)
    registro.cerrar(oid, precio_salida=103.0, motivo="objetivo")
    registro._con.execute(  # noqa: SLF001
        "UPDATE operaciones SET razon = ? WHERE id = ?",
        ("En realidad entré porque sabía que subía.", oid),
    )
    registro._con.commit()  # noqa: SLF001

    assert instantanea(registro)["sellosRotos"] == [oid]


def test_los_ejes_van_en_orden_alfabetico_y_no_por_resultado(registro: Registro) -> None:
    """Ordenar por R invita a elegir el mejor eje mirando la tabla, que es el
    sobreajuste que CRITERIO_ABORTO.md prohíbe."""
    for eje, salida in (("zone-reclaim", 104.0), ("anti-smc", 98.5), ("dip-trap", 101.0)):
        oid = _operacion(registro, eje=eje)
        registro.cerrar(oid, precio_salida=salida, motivo="manual")

    ejes = [e["eje"] for e in instantanea(registro)["porEje"]]

    assert ejes == sorted(ejes)
    assert ejes == ["anti-smc", "dip-trap", "zone-reclaim"]


def test_las_cerradas_van_de_vieja_a_nueva_aunque_se_recorten(registro: Registro) -> None:
    """El tope recorta las MÁS VIEJAS —son las que menos dicen del eje vigente—
    pero lo que queda se lee en orden."""
    for _ in range(4):
        oid = _operacion(registro)
        registro.cerrar(oid, precio_salida=103.0, motivo="objetivo")

    ids = [o["id"] for o in instantanea(registro, tope=3)["cerradas"]]

    assert ids == [2, 3, 4]


def test_el_contexto_viaja_como_objeto_y_no_como_texto(registro: Registro) -> None:
    """El panel filtra por régimen y por día de la semana. Con el JSON sin
    parsear tendría que hacerlo a mano y en la práctica no lo haría."""
    oid = _operacion(registro)
    registro.cerrar(oid, precio_salida=103.0, motivo="objetivo", contexto_salida=_contexto(103.0))

    cerrada = instantanea(registro)["cerradas"][0]

    assert cerrada["contexto"]["regimen_btc"] == "ascending"
    assert cerrada["contextoSalida"]["precio"] == 103.0


def test_sin_panel_configurado_devuelve_la_foto_sin_fallar(
    registro: Registro, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publicar tiene que poder probarse sin red, y un Codespace sin la variable
    puesta no debería perder la corrida entera por no poder avisar."""
    monkeypatch.delenv("PANEL_URL", raising=False)
    _operacion(registro)

    foto = publicar(registro)

    assert foto["abiertas"][0]["eje"] == "range-sweep"


def test_la_foto_es_json_serializable(registro: Registro) -> None:
    """Viaja por HTTP. Un dato que no serialice rompe la publicación entera y el
    error aparecería recién en el Codespace, a mitad de una sesión."""
    oid = _operacion(registro)
    registro.cerrar(oid, precio_salida=103.0, motivo="objetivo")

    assert json.loads(json.dumps(instantanea(registro)))["cerradas"][0]["id"] == oid


async def _publicar_con(tmp_path: Path, entorno: dict[str, str]) -> object:
    """Corre la herramienta con el entorno dado."""
    import os

    from tools.paper import build_paper_tools

    herramienta = {h.name: h for h in build_paper_tools(str(tmp_path / "ops.db"), 4000)}[
        "publicar_historial"
    ]
    antes = {k: os.environ.get(k) for k in entorno}
    os.environ.update(entorno)
    try:
        return await herramienta.run(herramienta.args_model())
    finally:
        for k, v in antes.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.mark.asyncio
async def test_sin_panel_no_es_un_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin PANEL_URL el registro sigue completo: no poder enseñarlo no es
    haberlo perdido, y decirlo como error haría que el agente reintentara."""
    monkeypatch.delenv("PANEL_URL", raising=False)
    r = await _publicar_con(tmp_path, {})

    assert r.summary == {"publicado": False}  # type: ignore[attr-defined]
    assert "no es un error" in r.content.lower()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_un_panel_caido_no_invalida_la_sesion(tmp_path: Path) -> None:
    """Las operaciones ya están registradas y selladas cuando esto corre. Si un
    fallo de red se propagara, una sesión entera de trabajo parecería perdida
    por no haber podido AVISAR de ella."""
    # El puerto 9 (discard) rechaza la conexión sin esperar.
    r = await _publicar_con(tmp_path, {"PANEL_URL": "http://127.0.0.1:9"})

    assert r.summary == {"publicado": False}  # type: ignore[attr-defined]
    assert "guardadas igual" in r.content  # type: ignore[attr-defined]
