# mcp_client/

Cliente MCP (Model Context Protocol): conecta servidores externos y suma sus
herramientas al registro del agente, junto a las nativas.

Se llama `mcp_client` y no `mcp` porque ese nombre es el del paquete oficial
(`pip install mcp`): una carpeta `mcp/` en la raíz del repo lo taparía, y el
`import mcp` del propio SDK resolvería a este directorio.

Solo se conectan servidores declarados en la configuración. Las descripciones
de sus herramientas son **contenido externo no confiable** (tool poisoning,
`docs/seguridad-byte.md`) y se envuelven antes de que las lea el modelo.
