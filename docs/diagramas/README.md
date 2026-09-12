# docs/diagramas/

Dos diagramas del sistema, como páginas HTML que se abren en el navegador:

| Archivo | Qué muestra |
|---|---|
| `arquitectura.html` | El sistema completo: de dónde entra un pedido hasta el modelo, las herramientas y la base |
| `n8n.html` | Las conexiones con n8n, en las dos direcciones |

Se abren con doble clic, sin servidor ni dependencias: cada uno es un archivo
solo. Traen recorridos guiados (arriba a la izquierda), búsqueda, modo oscuro y
exportación a PNG/SVG.

## Regenerarlos

Los `.json` de al lado son la fuente. Con la skill `archify`:

```bash
node <ruta-a-archify>/bin/archify.mjs deliver architecture \
  docs/diagramas/arquitectura.json docs/diagramas/arquitectura.html --quality showcase
```

Los dos pasan el perfil `showcase` (9 chequeos, 0 errores, 0 avisos) y la
verificación en navegador real a 1440×900 y 2048×1320, en claro y oscuro.

## Qué cuentan

**arquitectura.html** — el CLI, la web y los clientes de OpenAI entran por la
misma API; la API crea un run y el agente decide qué herramienta usar. Todo lo
que está dentro del recuadro corre en tu máquina. Los recorridos guiados separan
tres lecturas: una pregunta simple, las herramientas, y la memoria con RAG.

**n8n.html** — las dos direcciones. n8n le manda documentos y preguntas a Byte
(ingesta y correo), y Byte usa herramientas que viven en n8n (MCP). Ninguna de
las dos mete credenciales de terceros dentro de Byte, que es el punto de tenerlo
separado.
