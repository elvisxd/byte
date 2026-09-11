// Capturas de la web con un navegador real.
//
// Existe porque el layout no se verifica con tests: la primera versión de la
// Fase 5 servía bien todos los assets, no tenía errores de consola y los ids
// coincidían — y aun así la barra de escritura quedaba fuera de la pantalla,
// porque la sidebar estiraba el grid a 3238px de alto. Eso solo se ve mirando.
//
//   npm --prefix scripts install   (una vez)
//   BYTE_API_KEY=... node scripts/capturas.mjs [destino]
import { chromium } from "playwright";

const clave = process.env.BYTE_API_KEY;
const destino = process.argv[2] || "capturas";
const base = process.env.BYTE_URL || "http://localhost:8000";
if (!clave) {
  console.error("Falta BYTE_API_KEY");
  process.exit(1);
}

const navegador = await chromium.launch();
const pagina = await navegador.newPage({ viewport: { width: 1280, height: 860 } });

const problemas = [];
pagina.on("console", (m) => m.type() === "error" && problemas.push(m.text()));
pagina.on("pageerror", (e) => problemas.push("pageerror: " + e.message));

await pagina.goto(base, { waitUntil: "networkidle" });
await pagina.screenshot({ path: `${destino}/1-ingreso.png` });

await pagina.fill("#api-key", clave);
await pagina.click("#form-ingreso button[type=submit]");
await pagina.waitForSelector("#app:not([hidden])", { timeout: 15000 });
await pagina.waitForTimeout(1000);
await pagina.screenshot({ path: `${destino}/2-chat.png` });

await pagina.click("#ver-docs");
await pagina.waitForTimeout(1200);
await pagina.screenshot({ path: `${destino}/3-documentos.png` });

// Lo que las capturas no muestran: que todo entre en la ventana.
const alto = await pagina.evaluate(() => document.body.scrollHeight);
const ventana = await pagina.evaluate(() => window.innerHeight);
if (alto > ventana + 1) {
  problemas.push(`el layout desborda: body ${alto}px en una ventana de ${ventana}px`);
}

console.log(problemas.length ? "PROBLEMAS:\n  " + problemas.join("\n  ") : "sin problemas");
await navegador.close();
process.exit(problemas.length ? 1 : 0);
