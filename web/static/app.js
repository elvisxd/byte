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
  aprobacion: document.getElementById("aprobacion"),
  aprobacionMotivo: document.getElementById("aprobacion-motivo"),
  aprobacionCodigo: document.getElementById("aprobacion-codigo"),
  aprobar: document.getElementById("aprobar"),
  rechazar: document.getElementById("rechazar"),
  entrada: document.getElementById("entrada"),
  enviar: document.getElementById("enviar"),
  detener: document.getElementById("detener"),
  fuentes: document.getElementById("fuentes"),
};

let conversationId = null;
let runId = null;
let stream = null;
// Para retomar el stream donde quedó cuando el run se pausa y se reanuda.
let ultimoEventoId = 0;
let pendiente = null;
let destinoActual = null;

const PASOS = {
  retrieve_context: "Preparando contexto...",
  compact: "Resumiendo lo anterior...",
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

function escuchar(eventsUrl, destino, desdeId = 0) {
  const url = desdeId > 0 ? `${eventsUrl}?last_event_id=${desdeId}` : eventsUrl;
  stream = new EventSource(url, { withCredentials: true });
  destinoActual = destino;
  let texto = destino.textContent || "";

  const on = (tipo, manejador) =>
    stream.addEventListener(tipo, (evento) => {
      // El id sirve para retomar sin repetir si el run se pausa.
      if (evento.lastEventId) ultimoEventoId = Number(evento.lastEventId);
      manejador(evento);
    });

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
  on("STATE_DELTA", (evento) => {
    const datos = JSON.parse(evento.data);
    if (datos.awaiting_approval) {
      mostrarAprobacion(datos.awaiting_approval);
    } else if ("awaiting_approval" in datos) {
      el.aprobacion.hidden = true;
      pendiente = null;
    }
  });
  on("STATE_DELTA", (evento) => {
    const datos = JSON.parse(evento.data);
    if (datos.replay_incompleto) {
      // Faltan deltas de texto (reconexión tardía): el mensaje final se pide
      // por la API al terminar, así que acá solo se avisa.
      el.pasos.textContent = "Reconectado: recuperando la respuesta...";
    }
  });
  on("RUN_FINISHED", (evento) => {
    const datos = JSON.parse(evento.data);
    if (datos.status === "paused") {
      // El run sigue vivo esperando la decisión: no se limpia el estado.
      stream.close();
      stream = null;
      el.pasos.textContent = "Esperando tu confirmación...";
      return;
    }
    if (datos.status === "cancelled") {
      destino.textContent = texto + (texto ? "\n" : "") + "[detenido]";
    }
    const fuentes = datos.sources || [];
    el.fuentes.textContent = fuentes.length
      ? "Fuentes: " + fuentes.map((f) => f.url || f.filename).join(" · ")
      : "";
    // El texto definitivo es el que quedó guardado: si la reconexión perdió
    // deltas, esto lo deja completo igual.
    if (datos.message_id) {
      pedir(`/messages/${datos.message_id}`)
        .then((mensaje) => {
          if (mensaje?.content) destino.textContent = mensaje.content;
        })
        .catch(() => {
          /* si falla, queda lo que llegó por el stream */
        });
    }
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
  el.aprobacion.hidden = true;
  ultimoEventoId = 0;
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

function mostrarAprobacion(datos) {
  pendiente = datos;
  const motivos = {
    web_y_codigo_en_el_mismo_run:
      "Este run leyó páginas web y ahora quiere ejecutar código. Revisá el código antes de aprobar.",
    modo_seguro_activado: "Pediste modo seguro: confirmá antes de ejecutar.",
  };
  el.aprobacionMotivo.textContent = motivos[datos.reason] || datos.reason || "";
  // textContent, nunca innerHTML: esto es código que escribió el modelo.
  el.aprobacionCodigo.textContent = datos.code || "(sin código)";
  el.aprobacion.hidden = false;
  el.aprobar.disabled = false;
  el.rechazar.disabled = false;
}

async function decidir(aprobar) {
  if (!pendiente || !runId) return;
  el.aprobar.disabled = true;
  el.rechazar.disabled = true;
  const token = pendiente.resume_token;
  try {
    await pedir(`/runs/${runId}/resume`, {
      method: "POST",
      body: JSON.stringify({ resume_token: token, approve: aprobar }),
    });
    el.aprobacion.hidden = true;
    pendiente = null;
    // Mismo run: se retoma el stream desde el último evento visto.
    escuchar(`${API}/runs/${runId}/events`, destinoActual, ultimoEventoId);
  } catch (error) {
    el.pasos.textContent = "No se pudo continuar: " + error.message;
    el.aprobar.disabled = false;
    el.rechazar.disabled = false;
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
el.aprobar.addEventListener("click", () => decidir(true));
el.rechazar.addEventListener("click", () => decidir(false));
el.entrada.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) el.form.requestSubmit();
});
