"""Las herramientas de git: ver, commitear y subir (Fase 6).

Lo que más importa acá es el **orden**: mirar es gratis, commitear y subir
pasan por una decisión humana. Y que commit y push sean dos pasos separados —un
commit se deshace con `reset --soft` y nadie se entera; un push a un repo
público ya lo tiene GitHub.
"""

import subprocess
from pathlib import Path

import pytest

from tools.git import CommitArgs, EstadoArgs, _commit, _estado, build_git_tools


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Un repositorio de verdad, chico, para no simular git."""

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@ejemplo.com")
    git("config", "user.name", "Test")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "inicial")
    return tmp_path


def test_sin_cambios_lo_dice(repo: Path) -> None:
    """Proponer un commit sobre un árbol limpio sería inventar trabajo."""
    resultado = _estado(repo, EstadoArgs())
    assert "nada para commitear" in resultado.content
    assert resultado.summary["archivos"] == 0


def test_el_estado_trae_el_diff(repo: Path) -> None:
    """Sin el diff, el mensaje del commit sale de adivinar qué cambió."""
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    contenido = _estado(repo, EstadoArgs()).content
    assert "app.py" in contenido
    assert "-x = 1" in contenido and "+x = 2" in contenido


def test_el_diff_se_puede_omitir(repo: Path) -> None:
    """En un repo con mil cambios el diff no entra en el contexto."""
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    contenido = _estado(repo, EstadoArgs(con_diff=False)).content
    assert "app.py" in contenido
    assert "+x = 2" not in contenido


def test_commitear_deja_el_commit(repo: Path) -> None:
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    resultado = _commit(repo, CommitArgs(mensaje="Cambiar x"))
    assert resultado.ok is True
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"], capture_output=True, text=True, check=True
    ).stdout
    assert "Cambiar x" in log


def test_el_commit_avisa_que_no_subio_nada(repo: Path) -> None:
    """Es la distinción que hace útil separar commit de push: si el mensaje no
    lo dice, quien aprueba cree que ya está en GitHub."""
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    contenido = _commit(repo, CommitArgs(mensaje="Cambiar x")).content
    assert "no está en GitHub" in contenido or "Todavía no" in contenido


def test_no_se_commitea_sin_cambios(repo: Path) -> None:
    """Un commit vacío ensucia el historial sin decir nada."""
    assert _commit(repo, CommitArgs(mensaje="algo")).ok is False


def test_no_se_commitea_con_mensaje_vacio(repo: Path) -> None:
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    assert _commit(repo, CommitArgs(mensaje="   ")).ok is False


def test_las_tres_herramientas_se_arman(repo: Path) -> None:
    assert {h.name for h in build_git_tools(repo)} == {"git_estado", "git_commit", "git_push"}


def test_commit_y_push_piden_aprobacion_pero_mirar_no() -> None:
    """Mirar el diff es gratis y el modelo lo necesita para proponer un mensaje
    con fundamento; commitear y subir son decisiones."""
    from agent.graph import requiere_aprobacion

    assert requiere_aprobacion(["git_estado"], [], safe_mode=False) is None
    assert requiere_aprobacion(["git_commit"], [], safe_mode=False)
    assert requiere_aprobacion(["git_push"], [], safe_mode=False)


def test_la_aprobacion_del_push_dice_que_lo_hace_publico() -> None:
    """Aprobar un push sin saber que sube a GitHub es la diferencia entre
    arrepentirse a tiempo y reescribir historia."""
    from agent.graph import _que_va_a_hacer

    texto = _que_va_a_hacer({"name": "git_push", "args": {}})
    assert "GitHub" in texto
