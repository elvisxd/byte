// Servicio de sandbox: ejecuta el código que genera el agente, aislado.
//
// Corre en su propio servicio, sin secretos y sin dominio público: solo la API
// lo llama, por red privada y con un token interno (docs/seguridad-byte.md).
//
// Arranca únicamente con las protecciones puestas — ver `verificarProtecciones`.
// Se usa `npm start`, que ya incluye los flags.
import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";

import { LIMITES, SandboxError, ejecutar } from "./lib/runner.mjs";

const PUERTO = Number(process.env.PORT || 3000);
const HOST = process.env.SANDBOX_HOST || "0.0.0.0";
// Token compartido con la API. Sin token configurado no se atiende nada.
const TOKEN = process.env.SANDBOX_TOKEN || "";
// Cuántas ejecuciones a la vez. Cada una levanta un Pyodide propio (~100 MB),
// así que el número es a propósito chico.
const MAX_CONCURRENTES = Number(process.env.SANDBOX_MAX_CONCURRENT || 2);
const MAX_CUERPO_BYTES = LIMITES.maxCodeBytes + 2 * 1024;
const PAGINAS_WASM_ESPERADAS = 6144; // 384 MB

let enCurso = 0;

function log(evento, datos = {}) {
  console.log(JSON.stringify({ event: evento, ...datos }));
}

/**
 * Las dos protecciones que no se pueden aplicar por worker y tienen que venir
 * del proceso. Si falta alguna, el sandbox deja de ser un sandbox, así que el
 * servicio no arranca.
 */
function verificarProtecciones() {
  const faltan = [];

  // Sin generación de código dinámico no hay `js.eval("import('node:fs')")`,
  // que es un escape completo al filesystem del host. Chequeo funcional.
  try {
    new Function("return 1")();
    faltan.push("--disallow-code-generation-from-strings");
  } catch {
    /* bien: eval está desactivado */
  }

  // Tope de memoria del heap WASM: `resourceLimits` del worker NO lo cubre
  // (es memoria externa al heap de V8), así que una bomba de memoria se
  // alocaría entera. Acá se verifica el flag, porque medirlo de verdad
  // implicaría reservar los 384 MB al arrancar.
  const flags = [...process.execArgv, ...process.argv];
  if (!flags.some((f) => f.startsWith("--wasm-max-mem-pages="))) {
    faltan.push(`--wasm-max-mem-pages=${PAGINAS_WASM_ESPERADAS}`);
  }

  if (faltan.length > 0) {
    console.error(
      JSON.stringify({
        event: "sandbox_sin_protecciones",
        faltan,
        detalle: "arrancá con `npm start`, que ya incluye los flags",
      }),
    );
    process.exit(1);
  }
}

function tokenValido(recibido) {
  if (!TOKEN || !recibido) return false;
  const a = Buffer.from(recibido);
  const b = Buffer.from(TOKEN);
  // Comparación en tiempo constante; longitudes distintas se descartan antes.
  return a.length === b.length && timingSafeEqual(a, b);
}

function responder(res, status, cuerpo) {
  const texto = JSON.stringify(cuerpo);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(texto),
    "X-Content-Type-Options": "nosniff",
  });
  res.end(texto);
}

function error(res, status, code, message) {
  // En los errores de cuerpo puede quedar data sin leer en el socket: si se
  // reusa con keep-alive, el pedido siguiente del cliente falla. Se cierra.
  if (status === 400 || status === 413) res.setHeader("Connection", "close");
  responder(res, status, { error: { code, message } });
}

async function leerCuerpo(req) {
  const partes = [];
  let total = 0;
  for await (const parte of req) {
    total += parte.length;
    if (total > MAX_CUERPO_BYTES) {
      // Se drena lo que falta: si se corta el socket acá, el cliente ve un
      // error de conexión en vez del 413 que le estamos respondiendo.
      req.resume();
      throw new SandboxError("payload_too_large", "el cuerpo es demasiado grande", 413);
    }
    partes.push(parte);
  }
  if (total === 0) return {};
  try {
    return JSON.parse(Buffer.concat(partes).toString("utf8"));
  } catch {
    throw new SandboxError("bad_request", "el cuerpo no es JSON válido", 400);
  }
}

export function crearServidor() {
  return createServer(async (req, res) => {
    const { method, url } = req;

    if (method === "GET" && url === "/health") {
      // Público y mínimo: es lo que mira /health/details de la API.
      return responder(res, 200, { status: "ok", en_curso: enCurso });
    }

    if (method !== "POST" || url !== "/execute") {
      return error(res, 404, "not_found", "no existe");
    }

    if (!tokenValido(req.headers["x-internal-token"])) {
      return error(res, 401, "unauthorized", "token interno faltante o inválido");
    }

    if (enCurso >= MAX_CONCURRENTES) {
      res.setHeader("Retry-After", "2");
      return error(res, 429, "too_many_requests", "sandbox ocupado");
    }

    enCurso += 1;
    try {
      const cuerpo = await leerCuerpo(req);
      if (cuerpo.language !== undefined && cuerpo.language !== "python") {
        return error(res, 400, "bad_request", "solo se soporta python");
      }
      const resultado = await ejecutar(cuerpo.code, cuerpo.timeout_s);
      log("sandbox_ejecucion", {
        exit_code: resultado.exit_code,
        duration_ms: resultado.duration_ms,
        truncated: resultado.truncated,
  });
      return responder(res, 200, resultado);
    } catch (e) {
      if (e instanceof SandboxError) return error(res, e.status, e.code, e.message);
      log("sandbox_error_inesperado", { error_type: e?.name });
      return error(res, 500, "internal_error", "error interno del sandbox");
    } finally {
      enCurso -= 1;
    }
  });
}

// Al importarse desde los tests no arranca nada: solo cuando se ejecuta directo.
if (process.argv[1] && import.meta.url === `file://${process.argv[1]}`) {
  verificarProtecciones();
  if (!TOKEN) {
    console.error(
      JSON.stringify({
        event: "sandbox_sin_token",
        detalle: "definí SANDBOX_TOKEN: sin token el servicio rechaza todo",
      }),
    );
  }
  crearServidor().listen(PUERTO, HOST, () =>
    log("sandbox_arriba", { puerto: PUERTO, host: HOST, max_concurrentes: MAX_CONCURRENTES }),
  );
}
