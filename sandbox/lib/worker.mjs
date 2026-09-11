// Worker que ejecuta el código Python del agente dentro de Pyodide (WASM).
//
// Se crea uno nuevo por ejecución (docs/seguridad-byte.md: nada de estado entre
// corridas) y el padre lo mata al vencer el timeout.
//
// ORDEN DE ENDURECIMIENTO — importa, y cada paso se verificó contra un Pyodide
// sin endurecer donde el vector funcionaba (ver test/sandbox.test.mjs):
//
//   1. Borrar los globales de red ANTES de cargar Pyodide. Python no puede
//      recuperar una referencia a algo que ya no existe.
//   2. Cargar Pyodide (necesita `process.binding`, así que todavía no se toca).
//   3. Recién entonces neutralizar `process`: `dlopen` carga binarios nativos y
//      `binding` da acceso a internals de Node.
//   4. Sacar el importador de JS de `sys.meta_path` — defensa en profundidad,
//      NO una frontera: desde Python se puede restaurar vía
//      `_pyodide._importhook.jsfinder` o buscándolo con `gc`. Lo que de verdad
//      cierra el paso es lo de arriba, más el flag
//      --disallow-code-generation-from-strings del proceso (sin `eval` no hay
//      `import('node:fs')`, que era un escape completo al filesystem del host).
import { parentPort, workerData } from "node:worker_threads";

// --- 1) Puente de red ---
// Sin esto, `pyodide.http.pyfetch` sale a internet y el código del modelo puede
// exfiltrar lo que quiera.
const GLOBALES_DE_RED = [
  "fetch",
  "XMLHttpRequest",
  "WebSocket",
  "EventSource",
  "Request",
  "Response",
  "Headers",
  "FormData",
  "importScripts",
  "navigator",
];
for (const nombre of GLOBALES_DE_RED) {
  try {
    Object.defineProperty(globalThis, nombre, {
      value: undefined,
      writable: false,
      configurable: false,
    });
  } catch {
    try {
      globalThis[nombre] = undefined;
    } catch {
      /* no se pudo; el resto de las capas sigue en pie */
    }
  }
}

// --- 2) Carga de Pyodide ---
const { loadPyodide } = await import("pyodide");

const salida = [];
const errores = [];
let bytesSalida = 0;
const MAX_BYTES_SALIDA = workerData.maxOutputBytes;

function acumular(destino, texto) {
  // Se corta acá: una salida infinita no debe llenar la memoria del padre.
  if (bytesSalida >= MAX_BYTES_SALIDA) return;
  const restante = MAX_BYTES_SALIDA - bytesSalida;
  const recortado = texto.length > restante ? texto.slice(0, restante) : texto;
  bytesSalida += recortado.length;
  destino.push(recortado);
}

const pyodide = await loadPyodide({
  indexURL: workerData.indexURL,
  stdout: (linea) => acumular(salida, linea + "\n"),
  stderr: (linea) => acumular(errores, linea + "\n"),
});

// --- 3) `process` ---
// El worker ya arranca con `env: {}` (opción del Worker), así que
// `js.process.env` no tiene nada. Además se sacan los miembros peligrosos.
const MIEMBROS_PELIGROSOS = [
  "binding",
  "_linkedBinding",
  "dlopen",
  "mainModule",
  "argv",
  "argv0",
  "execPath",
  "execArgv",
  "kill",
  "abort",
  "exit",
  "reallyExit",
  "channel",
  "umask",
  "chdir",
  "setuid",
  "setgid",
];
for (const miembro of MIEMBROS_PELIGROSOS) {
  try {
    delete globalThis.process[miembro];
  } catch {
    /* algunos no son configurables según la versión de Node */
  }
}

// --- 4) Importador de JS (defensa en profundidad, no frontera) ---
// Va dentro de una función para no dejar nombres sueltos en el namespace
// donde después corre el código del usuario.
await pyodide.runPythonAsync(`
def _byte_cerrar_js():
    import sys
    sys.meta_path = [f for f in sys.meta_path if 'Js' not in type(f).__name__]
    for nombre in [k for k in sys.modules if k == 'js' or k.startswith('js.')]:
        del sys.modules[nombre]

_byte_cerrar_js()
del _byte_cerrar_js
`);

/**
 * Deja el traceback con lo que le sirve al modelo para corregirse.
 * Pyodide mete sus propios frames (`_pyodide/_base.py`, el `eval` interno): son
 * ruido y además exponen rutas internas.
 */
function limpiarTraceback(texto) {
  const lineas = String(texto).split("\n");
  const utiles = [];
  for (let i = 0; i < lineas.length; i += 1) {
    const linea = lineas[i];
    if (linea.includes("_pyodide/_base.py") || linea.includes("python314.zip/_pyodide")) {
      // También se descarta la línea de código que acompaña al frame.
      while (i + 1 < lineas.length && /^\s{4,}\S/.test(lineas[i + 1])) i += 1;
      continue;
    }
    utiles.push(linea);
  }
  return utiles.join("\n").replace(/\n{3,}/g, "\n\n");
}

// --- 5) Código del usuario ---
const arranque = performance.now();
let exitCode = 0;
try {
  await pyodide.runPythonAsync(workerData.code);
} catch (error) {
  exitCode = 1;
  // El traceback de Python le sirve al modelo para corregirse; va acotado.
  acumular(errores, limpiarTraceback(error?.message ?? error));
}

parentPort.postMessage({
  stdout: salida.join(""),
  stderr: errores.join(""),
  exit_code: exitCode,
  duration_ms: Math.round(performance.now() - arranque),
  truncated: bytesSalida >= MAX_BYTES_SALIDA,
});
