// Lanza un worker nuevo por ejecución y lo mata si se pasa del tiempo.
import { createRequire } from "node:module";
import { dirname } from "node:path";
import { Worker } from "node:worker_threads";

const require = createRequire(import.meta.url);
// Se resuelve el paquete local: Pyodide no debe ir a buscar nada a un CDN.
const INDEX_URL = dirname(require.resolve("pyodide"));
const WORKER = new URL("./worker.mjs", import.meta.url);

export const LIMITES = {
  // Los tres primeros son los del contrato (docs/api-contrato-byte.md).
  maxCodeBytes: 50 * 1024,
  maxTimeoutS: 30,
  maxOutputBytes: 64 * 1024,
  // Memoria del runtime WASM. Pyodide necesita unos 100 MB solo para arrancar.
  maxOldGenerationSizeMb: 256,
  maxYoungGenerationSizeMb: 32,
};

export class SandboxError extends Error {
  constructor(code, message, status = 400) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

/**
 * Ejecuta código Python en un intérprete nuevo.
 * Nunca lanza por culpa del código del usuario: un error de Python vuelve como
 * `exit_code: 1` con el traceback en `stderr`.
 */
export function ejecutar(code, timeoutS) {
  if (typeof code !== "string" || code.length === 0) {
    throw new SandboxError("bad_request", "code es obligatorio", 400);
  }
  if (Buffer.byteLength(code, "utf8") > LIMITES.maxCodeBytes) {
    throw new SandboxError("payload_too_large", "el código supera los 50 KB", 413);
  }
  const timeout = Math.min(Math.max(Number(timeoutS) || 10, 1), LIMITES.maxTimeoutS);

  return new Promise((resolve) => {
    const worker = new Worker(WORKER, {
      // Sin variables de entorno: desde Python se podía leer process.env entero.
      env: {},
      // Sin heredar los flags del padre: el worker solo necesita correr su
      // archivo. (El flag de V8 que desactiva `eval` es del proceso entero, así
      // que sigue aplicando acá.)
      execArgv: [],
      // Tope de memoria real del isolate: una bomba de memoria muere acá.
      resourceLimits: {
        maxOldGenerationSizeMb: LIMITES.maxOldGenerationSizeMb,
        maxYoungGenerationSizeMb: LIMITES.maxYoungGenerationSizeMb,
      },
      workerData: {
        code,
        indexURL: INDEX_URL,
        maxOutputBytes: LIMITES.maxOutputBytes,
      },
      stdout: true,
      stderr: true,
    });

    const arranque = performance.now();
    let resuelto = false;
    const terminar = (resultado) => {
      if (resuelto) return;
      resuelto = true;
      clearTimeout(reloj);
      worker.terminate();
      resolve(resultado);
    };

    const reloj = setTimeout(
      () =>
        terminar({
          stdout: "",
          stderr: `La ejecución pasó el límite de ${timeout} s y se cortó.`,
          exit_code: 124, // como `timeout(1)`
          duration_ms: Math.round(performance.now() - arranque),
          truncated: false,
        }),
      timeout * 1000,
    );

    worker.on("message", terminar);
    worker.on("error", (error) => {
      // El detalle queda en el log del servicio; al cliente va algo genérico.
      console.error(
        JSON.stringify({
          event: "sandbox_worker_error",
          error_type: error?.name,
          message: String(error?.message ?? error).slice(0, 300),
        }),
      );
      terminar({
        stdout: "",
        // Sin rutas internas ni stack traces del host para el cliente.
        stderr: /memory/i.test(String(error?.message))
          ? "La ejecución superó el límite de memoria."
          : "La ejecución falló en el sandbox.",
        exit_code: 1,
        duration_ms: Math.round(performance.now() - arranque),
        truncated: false,
      });
    });
    worker.on("exit", (codigo) => {
      if (codigo === 1) {
        // Salida por resourceLimits: el worker muere sin emitir 'error'.
        terminar({
          stdout: "",
          stderr: "La ejecución superó el límite de memoria.",
          exit_code: 137,
          duration_ms: Math.round(performance.now() - arranque),
          truncated: false,
        });
      }
    });
  });
}
