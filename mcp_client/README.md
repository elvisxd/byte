# mcp_client/

Cliente MCP (Model Context Protocol): conecta servidores externos y suma sus
herramientas al registro del agente, junto a las nativas.

Se llama `mcp_client` y no `mcp` porque ese nombre es el del paquete oficial
(`pip install mcp`): una carpeta `mcp/` en la raíz del repo lo taparía, y el
`import mcp` del propio SDK resolvería a este directorio.

Solo se conectan servidores declarados en la configuración. Las descripciones
de sus herramientas son **contenido externo no confiable** (tool poisoning,
`docs/seguridad-byte.md`) y se envuelven antes de que las lea el modelo.

## Cómo conectar un servidor

```bash
BYTE_MCP_SERVERS="clima=http://127.0.0.1:8898/mcp,n8n=http://n8n:5678/mcp"
BYTE_MCP_TIMEOUT_S=30
```

Formato `nombre=url`, separados por coma. Solo `http(s)`: `stdio` implicaría que
Byte lanza procesos, que es otra superficie de ataque y otra decisión.

Las herramientas aparecen en `GET /tools` y en el registro del agente junto a
las nativas, con `source: "mcp:<nombre>"`. Un servidor caído no impide arrancar:
Byte sigue con las herramientas que tenga, igual que arranca sin Tavily.

Un nombre repetido **no** pisa a una herramienta nativa: `code_exec` sigue
siendo el sandbox de Byte aunque un servidor externo declare otra con ese
nombre.

## Por qué la descripción no se envuelve y el resultado sí

El resultado de una herramienta viaja como mensaje al modelo, así que va
envuelto en los delimitadores de contenido no confiable, igual que una página
web o un documento.

La descripción no: va en el campo `description` de la definición de la
herramienta, que el modelo lee en *cada* decisión. Medido contra qwen3:8b, mismo
prompt y misma herramienta: **con la descripción envuelta, 0 de 3 llamadas; con
la descripción limpia, 3 de 3.** Los delimitadores son más largos que la
descripción y ahogan la señal.

En su lugar la descripción se sanea: se acota, se aplana a una línea (los saltos
dejan simular un turno nuevo), se neutralizan los delimitadores y se atribuye al
servidor (`[servidor MCP 'clima'] …`). Lo que no se puede resolver ahí es si el
texto miente sobre lo que hace la herramienta: para eso está la lista blanca —
solo servidores que alguien revisó.

## Probado a mano

Con un servidor MCP de verdad (`MCPServer` del SDK, transporte HTTP): Byte
arranca listando la herramienta externa, el modelo la llama y la respuesta usa
su resultado. El CLI la muestra como una línea de trabajo más
(`✓ Using clima_de · done`).
