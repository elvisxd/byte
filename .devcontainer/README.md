# .devcontainer/

Un Codespace para **medir modelos que no entran en una Mac de 16 GB**.

La pregunta que responde: *¿un modelo de 27-30B resuelve lo que granite4.1:8b no
puede?* Eso no se puede probar en local —no hay RAM— y comprar hardware o pagar
una API para averiguarlo es caro. Un codespace de 32 GB sí lo permite.

## Lo que hay que saber antes

**Codespaces no tiene GPU.** El modelo corre en CPU a 5-15 tokens por segundo,
contra los ~40 que hace granite en una Mac con Metal. Sirve para saber **si**
resuelve las tareas, no para cronometrarlo ni para usarlo a diario.

**Las horas se gastan por núcleo.** La máquina de 8 núcleos consume 8
core-hours por hora real: con los 180 de GitHub Pro son unas 22 horas al mes.
Alcanza de sobra para una tanda de evals, no para dejarlo prendido.

## Cómo se usa

Abrir el repo en Codespaces (el `postCreateCommand` instala todo), y después:

```bash
ollama serve &
ollama pull qwen3.6:27b                 # ~17 GB, tarda
uv run uvicorn api.main:app --port 8000 &
uv run python -m evals.correr --url http://localhost:8000 --api-key "$BYTE_API_KEY"
uv run python -m evals.correr --url http://localhost:8000 --api-key "$BYTE_API_KEY" --solo agentico
```

`evals/agentico.json` es el que importa: son las ocho tareas que granite falla
—leer una skill, ofrecer opciones, cambiar de estrategia— y lo que decide si un
modelo más grande vale la pena. La comparación anterior está en
`evals/COMPARACION.md`: granite 5/8, qwen3:14b 3/8.

**Apagar el codespace al terminar.** Sigue consumiendo horas mientras esté
prendido aunque no se use.
