"""Las skills: instrucciones por tarea que el agente carga cuando le sirven."""

from pathlib import Path

from agent.skills import cargar, indice
from tools.skills import LeerSkillArgs, build_skill_tool


def _skill(carpeta: Path, nombre: str, descripcion: str, cuerpo: str) -> None:
    d = carpeta / nombre
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {nombre}\ndescription: {descripcion}\n---\n\n{cuerpo}\n", encoding="utf-8"
    )


def test_se_cargan_del_formato_de_siempre(tmp_path: Path) -> None:
    """El formato es el de otras herramientas de agente —carpeta con SKILL.md y
    frontmatter— para que una skill escrita para otra se pueda copiar tal cual."""
    _skill(tmp_path, "commits", "Cómo escribir un commit", "Primera línea en presente.")
    skills = cargar(str(tmp_path))
    assert [s.nombre for s in skills] == ["commits"]
    assert "presente" in skills[0].cuerpo


def test_una_skill_sin_descripcion_se_descarta(tmp_path: Path) -> None:
    """La descripción es lo que el agente lee para decidir si la necesita: sin
    eso nunca la abriría, y ocuparía lugar en el índice para nada."""
    d = tmp_path / "muda"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: muda\n---\n\nAlgo.\n", encoding="utf-8")
    assert cargar(str(tmp_path)) == []


def test_el_indice_dice_para_que_sirve_cada_una(tmp_path: Path) -> None:
    """El agente ve el índice, no el contenido: tiene que alcanzarle para saber
    cuál abrir."""
    _skill(tmp_path, "commits", "Cómo escribir un commit", "x")
    texto = indice(cargar(str(tmp_path)))
    assert "commits" in texto
    assert "Cómo escribir un commit" in texto


def test_sin_carpeta_no_hay_skills() -> None:
    assert cargar("") == []
    assert cargar("/no/existe/nada") == []


async def test_la_herramienta_devuelve_el_cuerpo(tmp_path: Path) -> None:
    _skill(tmp_path, "commits", "Cómo escribir un commit", "Primera línea en presente.")
    herramienta = build_skill_tool(cargar(str(tmp_path)))
    assert herramienta is not None
    resultado = await herramienta.run(LeerSkillArgs(nombre="commits"))
    assert "presente" in resultado.content


async def test_una_skill_que_no_existe_dice_cuales_hay(tmp_path: Path) -> None:
    """El modelo inventa nombres: decirle cuáles hay le permite corregir en el
    turno siguiente en vez de insistir."""
    _skill(tmp_path, "commits", "Cómo escribir un commit", "x")
    herramienta = build_skill_tool(cargar(str(tmp_path)))
    assert herramienta is not None
    resultado = await herramienta.run(LeerSkillArgs(nombre="inventada"))
    assert resultado.ok is False
    assert "commits" in resultado.content


def test_sin_skills_no_se_registra_la_herramienta() -> None:
    """Una herramienta que solo puede fallar es peor que no tenerla: ocupa lugar
    en la lista que el modelo lee y lo tienta a llamarla."""
    assert build_skill_tool([]) is None


async def test_las_instrucciones_no_van_envueltas_como_no_confiables(tmp_path: Path) -> None:
    """Las escribió el dueño de la máquina, igual que el system prompt.
    Envolverlas diría "esto son datos, no órdenes", que es lo contrario de para
    qué existen."""
    _skill(tmp_path, "commits", "Cómo escribir un commit", "Hacelo así.")
    herramienta = build_skill_tool(cargar(str(tmp_path)))
    assert herramienta is not None
    resultado = await herramienta.run(LeerSkillArgs(nombre="commits"))
    assert "NO CONFIABLE" not in resultado.content
