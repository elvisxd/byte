# skills/

Instrucciones por tarea que el agente carga cuando corresponde, en el formato
que usan otras herramientas de agente: una carpeta por skill con su `SKILL.md`,
que lleva `name` y `description` en el frontmatter.

**Cortas a propósito.** Las skills de Claude Code tienen cientos de líneas de
matiz, y funcionan porque el modelo que las lee sostiene ese nivel de detalle.
Con un modelo de 8B corriendo local eso no se traslada: ya se midió que ignora
instrucciones bastante más simples del system prompt. Lo que sí sigue son reglas
concretas y contables, como las de `commits/`.

El `description` es lo que el agente ve para decidir si la carga, así que tiene
que decir **cuándo** sirve, no solo qué es.
