// Suite de escape del sandbox.
//
// Cada caso es un vector que FUNCIONABA contra un Pyodide sin endurecer: la red
// de Pyodide salía a internet, `js.process.env` exponía todas las variables de
// entorno y `js.eval("import('node:fs')")` leía cualquier archivo del host.
// Estos tests son la prueba de que siguen cerrados.
//
// Correr con: npm test  (el flag --disallow-code-generation-from-strings importa)
import assert from "node:assert/strict";
import { before, describe, it } from "node:test";

import { LIMITES, SandboxError, ejecutar } from "../lib/runner.mjs";

// Pyodide tarda ~2 s en arrancar y se crea un intérprete nuevo por ejecución.
const TIMEOUT_TEST = 60_000;

describe("ejecución normal", { timeout: TIMEOUT_TEST }, () => {
  it("corre Python y captura stdout", async () => {
    const r = await ejecutar("print('hola'); print(sum(range(10)))", 30);
    assert.equal(r.exit_code, 0);
    assert.equal(r.stdout, "hola\n45\n");
    assert.equal(r.stderr, "");
    assert.equal(r.truncated, false);
  });

  it("tiene la stdlib disponible", async () => {
    const r = await ejecutar(
      "import math, json, re, datetime, itertools\nprint(json.dumps({'raiz': math.sqrt(16)}))",
      30,
    );
    assert.equal(r.exit_code, 0);
    assert.match(r.stdout, /"raiz": 4/);
  });

  it("devuelve el traceback sin los frames internos de Pyodide", async () => {
    const r = await ejecutar("def f():\n    return 1/0\nf()", 30);
    assert.equal(r.exit_code, 1);
    assert.match(r.stderr, /ZeroDivisionError/);
    assert.match(r.stderr, /line 2, in f/);
    // Los frames de Pyodide son ruido para el modelo y exponen rutas internas.
    assert.doesNotMatch(r.stderr, /_pyodide/);
  });
});

// El código del modelo puede restaurar el importador de `js` (lo verifica el
// último test de este bloque), así que los tests de aislamiento lo restauran
// primero a propósito: así prueban las fronteras reales —red, env, eval,
// dlopen— y no la capa débil de `sys.meta_path`.
const RESTAURAR_JS = [
  "import sys",
  "try:",
  "    import _pyodide._importhook as ih",
  "    sys.meta_path.append(ih.jsfinder)",
  "except Exception:",
  "    pass",
  "",
].join("\n");

describe("aislamiento", { timeout: TIMEOUT_TEST }, () => {
  it("no puede leer el filesystem del host", async () => {
    const r = await ejecutar(
      "try:\n"
        + "    print(open('/etc/hostname').read())\n"
        + "except Exception as e:\n"
        + "    print('BLOQUEADO', type(e).__name__)",
      30,
    );
    assert.equal(r.exit_code, 0);
    assert.match(r.stdout, /BLOQUEADO/);
  });

  it("el importador de js no está disponible por defecto", async () => {
    // Primera capa (defensa en profundidad): sin ella el modelo llega a `js`
    // sin esfuerzo. Es esquivable, y por eso los demás tests no dependen de ella.
    const r = await ejecutar("import js\nprint('IMPORTO JS')", 30);
    assert.equal(r.exit_code, 1);
    assert.match(r.stderr, /No module named 'js'/);
  });

  it("sin red, incluso con el importador de js restaurado", async () => {
    const r = await ejecutar(
      RESTAURAR_JS
        + "import js\n"
        + "print('fetch:', getattr(js, 'fetch', None))\n"
        + "print('xhr:', getattr(js, 'XMLHttpRequest', None))\n"
        + "print('ws:', getattr(js, 'WebSocket', None))",
      30,
    );
    assert.equal(r.exit_code, 0, r.stderr);
    // Los globales de red se borran antes de cargar Pyodide: Python no puede
    // recuperar una referencia a algo que ya no existe.
    assert.match(r.stdout, /fetch: None/);
    assert.match(r.stdout, /xhr: None/);
    assert.match(r.stdout, /ws: None/);
  });

  it("pyfetch no puede salir a internet", async () => {
    const r = await ejecutar(
      RESTAURAR_JS
        + "try:\n"
        + "    from pyodide.http import pyfetch\n"
        + "    await pyfetch('https://example.com')\n"
        + "    print('SALIO A INTERNET')\n"
        + "except Exception as e:\n"
        + "    print('BLOQUEADO', type(e).__name__)",
      30,
    );
    assert.match(r.stdout, /BLOQUEADO/);
    assert.doesNotMatch(r.stdout, /SALIO A INTERNET/);
  });

  it("no puede generar código dinámico, ni con js restaurado", async () => {
    // Esto es lo que cierra el escape completo: js.eval("import('node:fs')")
    // leía cualquier archivo del host.
    const r = await ejecutar(
      RESTAURAR_JS
        + "import js\n"
        + "try:\n"
        + "    js.eval('1+1')\n"
        + "    print('EVAL FUNCIONA')\n"
        + "except Exception as e:\n"
        + "    print('BLOQUEADO', type(e).__name__)",
      30,
    );
    assert.equal(r.exit_code, 0, r.stderr);
    assert.match(r.stdout, /BLOQUEADO/);
    assert.doesNotMatch(r.stdout, /EVAL FUNCIONA/);
  });

  it("no puede leer el filesystem por import dinámico", async () => {
    const r = await ejecutar(
      RESTAURAR_JS
        + "import js\n"
        + "try:\n"
        + "    m = js.eval(\"import('node:fs')\")\n"
        + "    print('ESCAPE')\n"
        + "except Exception as e:\n"
        + "    print('BLOQUEADO', type(e).__name__)",
      30,
    );
    assert.match(r.stdout, /BLOQUEADO/);
    assert.doesNotMatch(r.stdout, /ESCAPE/);
  });

  it("no ve variables de entorno, ni con js restaurado", async () => {
    const r = await ejecutar(
      RESTAURAR_JS
        + "import os, js\n"
        + "print('os.environ:', dict(os.environ))\n"
        + "print('js.process.env:', js.process.env)",
      30,
    );
    assert.equal(r.exit_code, 0, r.stderr);
    // Ni las del sandbox ni las del host: el worker arranca con env vacío.
    assert.doesNotMatch(r.stdout, /BYTE_|TAVILY|DATABASE_URL|SANDBOX_TOKEN/);
  });

  it("no puede cargar binarios nativos, ni con js restaurado", async () => {
    const r = await ejecutar(
      RESTAURAR_JS
        + "import js\n"
        + "print('dlopen:', getattr(js.process, 'dlopen', None))\n"
        + "print('binding:', getattr(js.process, 'binding', None))",
      30,
    );
    assert.equal(r.exit_code, 0, r.stderr);
    assert.match(r.stdout, /dlopen: None/);
    assert.match(r.stdout, /binding: None/);
  });
});

describe("límites", { timeout: TIMEOUT_TEST }, () => {
  it("corta un loop infinito por timeout", async () => {
    const arranque = Date.now();
    const r = await ejecutar("while True:\n    pass", 2);
    assert.equal(r.exit_code, 124);
    assert.match(r.stderr, /límite de 2 s/);
    // Que haya cortado de verdad, no que haya esperado el timeout del test.
    assert.ok(Date.now() - arranque < 20_000);
  });

  it("recorta la salida enorme", async () => {
    const r = await ejecutar("print('x' * 500_000)", 30);
    assert.equal(r.truncated, true);
    assert.ok(r.stdout.length <= LIMITES.maxOutputBytes);
  });

  it("rechaza código que pasa el tamaño máximo", () => {
    assert.throws(() => ejecutar("x = 1\n".repeat(20_000), 10), (error) => {
      assert.ok(error instanceof SandboxError);
      assert.equal(error.code, "payload_too_large");
      assert.equal(error.status, 413);
      return true;
    });
  });

  it("corta una bomba de memoria", async () => {
    // `resourceLimits` del worker NO alcanza: la memoria del heap WASM es
    // externa al heap de V8 y sin el flag --wasm-max-mem-pages esta
    // asignación de 2 GB se completaba sin problema.
    const r = await ejecutar("x = bytearray(2_000_000_000)\nprint('ALOCO 2GB')", 25);
    assert.doesNotMatch(r.stdout, /ALOCO 2GB/);
    assert.match(r.stderr, /MemoryError/);
  });

  it("no deja estado entre ejecuciones", async () => {
    const primera = await ejecutar("secreto = 'de la ejecución anterior'\nprint('guardado')", 30);
    assert.equal(primera.exit_code, 0);
    const segunda = await ejecutar(
      "try:\n    print(secreto)\nexcept NameError:\n    print('SIN ESTADO PREVIO')",
      30,
    );
    assert.match(segunda.stdout, /SIN ESTADO PREVIO/);
  });
});
