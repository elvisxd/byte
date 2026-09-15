"""La rúbrica cuenta lo que el criterio pregunta, sobre trazas recortadas y registros."""

import json
from datetime import UTC, datetime
from pathlib import Path

from paper.registro import Contexto, Registro
from paper.rubrica import rubrica_de_registro, rubrica_de_trazas


def _traza(tmp_path: Path, nombre: str, pasos: list[dict]) -> Path:
    p = tmp_path / f"vigia-{nombre}-1.json"
    p.write_text(json.dumps({"modelo": nombre, "pasos": pasos}))
    return p


def test_reaccion_al_rechazo_y_tope_sin_registrar(tmp_path: Path) -> None:
    rechazo = '{"ok": false, "error": "no se registró"}'
    pasos = [
        # vuelta 1: rechazan la predicción y REPITE el mismo nivel en el mismo marco
        {"vuelta": 1, "tipo": "herramienta", "nombre": "predecir"},
        {
            "vuelta": 1,
            "tipo": "argumentos",
            "texto": '{"nivel": 77000, "temporalidad": "4h", "razonamiento": "x… (+300)',
        },
        {"vuelta": 1, "tipo": "resultado", "texto": rechazo},
        {"vuelta": 1, "tipo": "herramienta", "nombre": "predecir"},
        {"vuelta": 1, "tipo": "argumentos", "texto": '{"nivel": 77000, "temporalidad": "4h"}'},
        {"vuelta": 1, "tipo": "resultado", "texto": rechazo},
        {"vuelta": 1, "tipo": "texto", "texto": "Alcancé el límite de 6 iteraciones sin terminar."},
        # vuelta 2: rechazan y BAJA de marco
        {
            "vuelta": 2,
            "tipo": "pensamiento",
            "texto": "range-sweep: no. dip-trap: no. anti-smc: sí.",
        },
        {"vuelta": 2, "tipo": "herramienta", "nombre": "predecir"},
        {"vuelta": 2, "tipo": "argumentos", "texto": '{"nivel": 77000, "temporalidad": "4h"}'},
        {"vuelta": 2, "tipo": "resultado", "texto": rechazo},
        {"vuelta": 2, "tipo": "herramienta", "nombre": "predecir"},
        {"vuelta": 2, "tipo": "argumentos", "texto": '{"nivel": 77000, "temporalidad": "15m"}'},
        {"vuelta": 2, "tipo": "resultado", "texto": '{"ok": true, "id": 3}'},
        {"vuelta": 2, "tipo": "texto", "texto": "Alcancé el límite de 6 iteraciones."},
    ]
    r = rubrica_de_trazas([_traza(tmp_path, "gemini", pasos)])
    assert r["vueltas"] == 2
    assert r["rechazos"] == 2 and r["insiste"] == 1 and r["reacciona"] == 1
    # La vuelta 1 chocó con el tope sin registrar; la 2 chocó pero registró.
    assert r["tope_sin_registrar"] == 1
    assert r["pensamientos"] == 1 and r["recorren_ejes"] == 1


def test_el_registro_cuenta_razones_y_cierres_fuera_de_marco(tmp_path: Path) -> None:
    registro = Registro(tmp_path / "r.db")
    ctx = Contexto(
        precio=100.0,
        timestamp="2026-09-15T10:00:00+00:00",
        dia_semana=1,
        hora_utc=10,
        extra={"indicadores": {"atr": 2.0}},
    )
    i1 = registro.abrir(
        eje="range-sweep",
        simbolo="BTCUSDT",
        direccion="long",
        contexto=ctx,
        razon="a",
        stop_loss=98.0,
        take_profit=104.0,
        temporalidad="4h",
    )
    # Cerrada a los 17 minutos de una tesis de 4h: 0,07 velas de su marco.
    registro.cerrar(
        i1,
        precio_salida=101.0,
        motivo="manual",
        contexto_salida=ctx,
        analisis="me asusté",
        cuando=datetime(2026, 9, 15, 10, 17, tzinfo=UTC),
    )
    r = rubrica_de_registro(tmp_path / "r.db")
    assert r["cierres_manuales"] == 1 and r["manuales_antes_de_una_vela"] == 1
    assert r["total"] == 1 and r["distintas"] == 1
