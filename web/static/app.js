// Cliente mínimo del MVP: consume los eventos AG-UI por SSE.
//
// Seguridad: todo el texto se pinta con textContent, nunca innerHTML. El
// Markdown sanitizado llega en la Fase 5 junto con la UI real.
"use strict";

const API = "/api/v1";

const el = {
  estado: document.getElementById("estado"),
  login: document.getElementById("login"),
  apiKey: document.getElementById("api-key"),
  entrar: document.getElementById("entrar"),
  chat: document.getElementById("chat"),
  mensajes: document.getElementById("mensajes"),
  pasos: document.getElementById("pasos"),
  form: document.getElementById("form-mensaje"),
  entrada: document.getElementById("entrada"),
  enviar: document.getElementById("enviar"),
  detener: document.getElementById("detener"),
  fuentes: document.getElementById("fuentes"),
};

let conversationId = null;
let runId = null;
let stream = null;

const PASOS = {
  retrieve_context: "Preparando contexto...",
  agent: "Pensando...",
  tools: "Usando herramientas...",
  finalize: "Cerrando...",
};

function burbuja(rol, texto) {
  const li = document.createElement("li");
  li.className = rol;
  const etiqueta = document.createElement("span");
  etiqueta.className = "rol";
  etiqueta.textContent = rol === "user" ? "vos" : "byte";
  const cuerpo = document.createElement("span");
  cuerpo.textContent = texto;
  li.append(etiqueta, cuerpo);
  el.mensajes.append(li);
  li.scrollIntoView({ block: "end" });
  return cuerpo;
}

async function pedir(ruta, opciones = {}) {
  const respuesta = await fetch(API + ruta, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...opciones,
  });
  if (!respuesta.ok) {
    let detalle = respuesta.statusText;
    try {
      const cuerpo = await respuesta.json();
      detalle = cuerpo?.error?.message || detalle;
    } catch (_) {
      /* respuesta sin JSON */
    }
    throw new Error(detalle);
  }
  return respuesta.status === 204 ? null : respuesta.json();
}

async function entrar() {
  el.entrar.disabled = true;
  try {
    // La API key se canjea por una cookie httpOnly: EventSource no manda headers.
    await pedir("/session", {
      method: "POST",
      body: JSON.stringify({ api_key: el.apiKey.value }),
    });
    el.apiKey.value = "";
    const conversacion = await pedir("/conversations", {
      method: "POST",
      body: JSON.stringify({}),
    });
    conversationId = conversacion.id;
    el.login.hidden = true;
    el.chat.hidden = false;
    el.entrada.focus();
    await mostrarEstado();
  } catch (error) {
    el.estado.textContent = "No se pudo entrar: " + error.message;
  } finally {
    el.entrar.disabled = false;
  }
}

async function mostrarEstado() {
  try {
    const salud = await pedir("/health/details");
    el.estado.textContent =
      salud.status === "ok"
        ? `En línea, corriendo local (${salud.model})`
        : `Degradado — ollama: ${salud.ollama}, db: ${salud.db}`;
  } catch (error) {
    el.estado.textContent = "Estado desconocido: " + error.message;
  }
}

function escuchar(eventsUrl, destino) {
  stream = new EventSource(eventsUrl, { withCredentials: true });
  let texto = "";

  const on = (tipo, manejador) => stream.addEventListener(tipo, manejador);

  on("STEP_STARTED", (evento) => {
    const paso = JSON.parse(evento.data).stepName;
    el.pasos.textContent = PASOS[paso] || paso;
  });
  on("TOOL_CALL_START", (evento) => {
    el.pasos.textContent = `Usando ${JSON.parse(evento.data).toolCallName}...`;
  });
  on("TEXT_MESSAGE_CONTENT", (evento) => {
    texto += JSON.parse(evento.data).delta;
    destino.textContent = texto;
  });
  on("RUN_FINISHED", (evento) => {
    const datos = JSON.parse(evento.data);
    if (datos.status === "cancelled") {
      destino.textContent = texto + (texto ? "\n" : "") + "[detenido]";
    }
    const fuentes = datos.sources || [];
    el.fuentes.textContent = fuentes.length
      ? "Fuentes: " + fuentes.map((f) => f.url || f.filename).join(" · ")
      : "";
    terminar();
  });
  on("RUN_ERROR", (evento) => {
    const datos = JSON.parse(evento.data);
    destino.textContent = texto + `\n[error: ${datos.message || datos.code}]`;
    terminar();
  });
  stream.onerror = () => {
    // EventSource reconecta solo y manda Last-Event-ID; si el run ya terminó,
    // el servidor cierra y acá se corta.
    if (stream && stream.readyState === EventSource.CLOSED) terminar();
  };
}

function terminar() {
  if (stream) {
    stream.close();
    stream = null;
  }
  runId = null;
  el.pasos.textContent = "";
  el.enviar.disabled = false;
  el.detener.disabled = true;
}

async function enviar(evento) {
  evento.preventDefault();
  const contenido = el.entrada.value.trim();
  if (!contenido || !conversationId || runId) return;

  burbuja("user", contenido);
  el.entrada.value = "";
  el.enviar.disabled = true;
  el.detener.disabled = false;
  el.fuentes.textContent = "";
  const destino = burbuja("assistant", "");

  try {
    const aceptado = await pedir(`/conversations/${conversationId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content: contenido }),
    });
    runId = aceptado.run_id;
    escuchar(aceptado.events_url, destino);
  } catch (error) {
    destino.textContent = "[error: " + error.message + "]";
    terminar();
  }
}

async function detener() {
  if (!runId) return;
  el.detener.disabled = true;
  try {
    await pedir(`/runs/${runId}/cancel`, { method: "POST" });
  } catch (error) {
    el.pasos.textContent = "No se pudo detener: " + error.message;
  }
}

el.entrar.addEventListener("click", entrar);
el.apiKey.addEventListener("keydown", (e) => {
  if (e.key === "Enter") entrar();
});
el.form.addEventListener("submit", enviar);
el.detener.addEventListener("click", detener);
el.entrada.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) el.form.requestSubmit();
});
