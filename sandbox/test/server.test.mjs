// El servicio HTTP: autenticación con el token interno y validación de entrada.
// La ejecución en sí está cubierta en sandbox.test.mjs.
import assert from "node:assert/strict";
import { after, before, describe, it } from "node:test";

process.env.SANDBOX_TOKEN = "token-de-prueba";
const { crearServidor } = await import("../server.mjs");

describe("servicio HTTP", { timeout: 60_000 }, () => {
  let servidor;
  let base;

  before(async () => {
    servidor = crearServidor();
    await new Promise((resolve) => servidor.listen(0, "127.0.0.1", resolve));
    base = `http://127.0.0.1:${servidor.address().port}`;
  });

  after(() => servidor?.close());

  const pedir = (ruta, opciones = {}) => fetch(base + ruta, opciones);
  const ejecutarHttp = (cuerpo, token = "token-de-prueba") =>
    pedir("/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Internal-Token": token },
      body: JSON.stringify(cuerpo),
    });

  it("/health responde sin token", async () => {
    const r = await pedir("/health");
    assert.equal(r.status, 200);
    assert.equal((await r.json()).status, "ok");
  });

  it("rechaza sin token", async () => {
    const r = await pedir("/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: "print(1)" }),
    });
    assert.equal(r.status, 401);
    assert.equal((await r.json()).error.code, "unauthorized");
  });

  it("rechaza un token incorrecto", async () => {
    const r = await ejecutarHttp({ code: "print(1)" }, "otro-token");
    assert.equal(r.status, 401);
  });

  it("rechaza un token con el largo correcto pero distinto", async () => {
    // La comparación es en tiempo constante, pero igual debe fallar.
    const r = await ejecutarHttp({ code: "print(1)" }, "token-de-pruebA");
    assert.equal(r.status, 401);
  });

  it("ejecuta con token válido", async () => {
    const r = await ejecutarHttp({ language: "python", code: "print(6 * 7)" });
    assert.equal(r.status, 200);
    const cuerpo = await r.json();
    assert.equal(cuerpo.exit_code, 0);
    assert.equal(cuerpo.stdout, "42\n");
  });

  it("rechaza otros lenguajes", async () => {
    const r = await ejecutarHttp({ language: "javascript", code: "1" });
    assert.equal(r.status, 400);
  });

  it("rechaza código que pasa el tamaño máximo", async () => {
    const r = await ejecutarHttp({ code: "x = 1\n".repeat(20_000) });
    assert.equal(r.status, 413);
    assert.equal((await r.json()).error.code, "payload_too_large");
  });

  it("rechaza un cuerpo que no es JSON", async () => {
    const r = await pedir("/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Internal-Token": "token-de-prueba" },
      body: "{esto no es json",
    });
    assert.equal(r.status, 400);
  });

  it("404 en rutas desconocidas", async () => {
    assert.equal((await pedir("/lo-que-sea")).status, 404);
  });
});
