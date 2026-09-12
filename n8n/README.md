# n8n/

Workflows exportados (JSON) para conectar n8n con Byte. Módulo **opcional**:
Byte funciona entero sin esto.

Son tres, y van en las dos direcciones:

| Archivo | Qué hace | Dirección |
|---|---|---|
| `ingesta-documentos.json` | Un archivo nuevo en una carpeta entra al RAG solo | n8n → Byte |
| `canal-email.json` | Se le pregunta a Byte por correo y responde | n8n → Byte |
| `servidor-mcp.json` | n8n expone herramientas que Byte puede usar | Byte → n8n |

## Levantar n8n

Va en un perfil aparte, así que `docker compose up` no lo arranca:

```bash
cd docker
docker compose --profile n8n up -d n8n
```

Hace falta definir en el `.env`:

```bash
N8N_PASSWORD=algo-largo            # el usuario por defecto es `byte`
N8N_ENCRYPTION_KEY=<openssl rand -hex 16, una vez>
BYTE_EMAIL_PERMITIDOS=vos@ejemplo.com    # solo si usás el canal de email
```

La clave de cifrado se genera **una vez** y queda fija en el `.env`. n8n la
guarda dentro de su volumen y compara en cada arranque: si cambia, no levanta
—`Mismatching encryption keys`— y hay que borrar el volumen y volver a cargar
todo. Ponerla con `$(openssl rand …)` directo en el compose da una distinta cada
vez, que es justo lo que rompe.

Queda en http://localhost:5678, publicado solo en `127.0.0.1` como el resto de
los servicios: n8n guarda credenciales de terceros y no tiene por qué escuchar
en la red local.

## Importar los workflows

Desde la UI (**Workflows → … → Import from File**) o por línea de comandos:

```bash
docker cp n8n byte-n8n-1:/tmp/workflows
docker exec byte-n8n-1 n8n import:workflow --separate --input=/tmp/workflows
docker restart byte-n8n-1   # los workflows activos se cargan al arrancar
```

Quedan **desactivados** a propósito: cada uno necesita sus credenciales antes de
servir para algo, y un workflow activo a medio configurar falla en cada
ejecución.

## Las credenciales van en n8n, nunca en el JSON

Los archivos de acá se versionan, así que no llevan ni una clave. Cada nodo que
necesita una lo dice en sus notas. Las que hacen falta:

- **Header Auth** con `X-API-Key` = tu `BYTE_API_KEY` — para subir documentos.
- **Header Auth** con `Authorization` = `Bearer <BYTE_API_KEY>` — para el canal
  de email, que usa el endpoint compatible con OpenAI.
- **IMAP y SMTP** de una casilla dedicada, si usás el canal de email.

## Que Byte use las herramientas de n8n

El workflow `servidor-mcp.json` publica un MCP Server Trigger. Una vez
publicado, su URL de producción va en el `.env` de Byte:

```bash
BYTE_MCP_SERVERS=n8n=http://localhost:5678/mcp/byte
BYTE_MCP_TOKENS=n8n=<el token Bearer del nodo>
```

El token va en su propia variable y no pegado a la URL: es un secreto, y
mezclarlo con la lista de servidores lo dejaría a la vista en cualquier log o
captura de la configuración.

Las herramientas aparecen en `GET /tools` y en el registro del agente con
`source: "mcp:n8n"`, junto a las nativas. En el CLI se ven como una línea de
trabajo más:

```
❯ que hora es en el equipo de n8n? usa la herramienta
✓ Using hora_del_equipo · done

La hora en el equipo de n8n es 2026-09-12T00:51:37.034Z.
```

## Seguridad

Lo que el checklist pedía (`docs/seguridad-byte.md`, Fase 3), y por qué:

- **n8n con autenticación y solo en `127.0.0.1`.** Del otro lado hay
  credenciales de servicios reales; una instancia abierta las regala.
- **Credenciales con el mínimo permiso.** La de Byte solo necesita subir
  documentos y preguntar; la de correo, una casilla dedicada y una contraseña de
  aplicación, no la del correo personal.
- **El canal de email filtra por remitente.** Sin eso, cualquiera que sepa la
  dirección le gasta el modelo, le hace buscar en la web y le llena el
  historial. Es la condición más importante de ese workflow.
- **Las descripciones de las herramientas MCP son texto de afuera.** Byte las
  sanea y las atribuye (`[servidor MCP 'n8n'] …`), pero eso protege del prompt
  injection, no de conectar un servidor que hace algo distinto de lo que dice:
  por eso la lista blanca, y por eso conviene leer lo que uno conecta.
- **El modo seguro no viaja por estos canales.** Si un run necesita aprobación
  humana, el correo no sirve para pedirla: el workflow avisa y deja el pedido
  para la web o el CLI.

## Probado a mano

Con n8n 2.38.7 en el contenedor del compose: los tres workflows importan sin
errores ni nodos desconocidos, y el servidor MCP se verificó de punta a punta —
Byte conecta, lista las herramientas y el agente llama una y usa su resultado.

Los sub-workflows de `servidor-mcp.json` (`agendar_recordatorio`,
`buscar_en_planillas`) quedan **sin implementar**: qué calendario y qué planilla
son de cada uno, y arrastrar esas credenciales en un JSON versionado sería
filtrarlas. Sirven como plantilla de cómo declarar una herramienta.
