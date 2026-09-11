# sandbox/

Servicio que ejecuta el código Python que genera el agente, aislado del host.

Python corre dentro de **Pyodide** (CPython compilado a WebAssembly), en un
worker de Node que se crea nuevo para cada ejecución. Docker-in-Docker y Piston
quedaron descartados en el diseño: los dos necesitan modo privilegiado y Railway
lo prohíbe (ver `docs/plan-asistente-ia-local.md`).

## Cómo correrlo

```bash
cd sandbox
npm install
SANDBOX_TOKEN=$(openssl rand -hex 32) npm start   # escucha en :3000
npm test                                          # 25 tests
```

`npm start` **no** es `node server.mjs`: los dos flags que lleva son parte de la
seguridad, y el servicio se niega a arrancar sin ellos.

## API

`POST /execute` con `X-Internal-Token` → `{ stdout, stderr, exit_code, duration_ms, truncated }`

```bash
curl -s -X POST localhost:3000/execute \
  -H "X-Internal-Token: $SANDBOX_TOKEN" -H 'Content-Type: application/json' \
  -d '{"language":"python","code":"print(sum(range(10)))","timeout_s":10}'
```

`exit_code` es `0` si el código corrió, `1` si Python tiró una excepción (el
traceback va en `stderr`, ya limpio de los frames internos de Pyodide), `124` si
se cortó por timeout y `137` si se pasó de memoria.

`GET /health` → `{ "status": "ok", "en_curso": 0 }`, sin token.

## Qué lo mantiene cerrado

Cada capa se verificó contra un Pyodide sin endurecer, donde el vector
**funcionaba**. Los tests de `test/sandbox.test.mjs` son la prueba de que siguen
cerrados.

| Capa | Qué evita | Por qué Python no puede deshacerla |
|---|---|---|
| Globales de red borrados antes de cargar Pyodide | `pyfetch` salía a internet: exfiltración | No se puede recuperar una referencia a algo que ya no existe |
| `--disallow-code-generation-from-strings` | `js.eval("import('node:fs')")` leía cualquier archivo del host | Es un flag del proceso, fuera del alcance del código |
| Worker con `env: {}` | `js.process.env` exponía todas las variables del servicio | El env del worker se fija al crearlo |
| `process.dlopen`/`binding` eliminados después de cargar | Cargar binarios nativos | No se puede recuperar una función borrada |
| `--wasm-max-mem-pages=6144` (384 MB) | Bomba de memoria: `bytearray(2_000_000_000)` se alocaba entero | Flag del proceso |
| Worker nuevo por ejecución + `terminate()` | Estado entre corridas de distintos usuarios; loops infinitos | El padre decide |
| `resourceLimits` del worker | Crecimiento del heap de **JS** | — |

**Lo que NO es una frontera:** sacar el importador de JS de `sys.meta_path`.
Está puesto igual (defensa en profundidad, hace falta esfuerzo extra para
llegar a `js`), pero desde Python se puede restaurar con
`_pyodide._importhook.jsfinder` o buscándolo con `gc`. Por eso los tests de
aislamiento **restauran el acceso a `js` a propósito** antes de verificar cada
límite: prueban las fronteras reales, no esta.

Y lo que tampoco: `resourceLimits` del worker no acota la memoria del heap WASM,
que es externa al heap de V8. Sin el flag de páginas, la bomba de memoria pasaba.

## Límites

| Qué | Valor | De dónde sale |
|---|---|---|
| Tamaño del código | 50 KB | contrato de la API |
| Timeout | 30 s máximo, 10 s por defecto | contrato de la API |
| Salida | 64 KB, se recorta | contrato de la API |
| Memoria | 384 MB de heap WASM | medido: Pyodide arranca con ~100 MB |
| Ejecuciones simultáneas | 2 (`SANDBOX_MAX_CONCURRENT`) | cada una es un Pyodide propio |

## Despliegue

- Sin dominio público: solo la API lo llama, por red privada y con
  `SANDBOX_TOKEN`.
- **Sin secretos en el entorno.** El token del propio servicio tampoco es
  visible desde adentro (el worker arranca con `env: {}`), y hay un test que lo
  verifica.
- Usuario sin root en la imagen.

## Paquetes de Python

Solo la stdlib. Pyodide trae los paquetes extra (numpy, pandas) desde un CDN, y
la red está cortada a propósito. Para habilitar alguno hay que bajar su wheel en
el build y cargarlo desde el filesystem del worker — queda para cuando alguna
tarea real lo pida, no antes.

El Python de acá es el que empaqueta Pyodide (3.14 en WASM) y no tiene relación
con el de la API (3.13): son dos servicios distintos.
