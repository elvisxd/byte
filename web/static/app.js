// Cliente web de Byte: consume los eventos AG-UI por SSE.
//
// Seguridad: todo el texto se pinta con textContent, nunca innerHTML. Lo que
// llega del modelo, de las herramientas y de los documentos es contenido no
// confiable, así que no puede interpretarse como HTML.
"use strict";

const API = "/api/v1";

const el = {
  // Ingreso
  ingreso: document.getElementById("ingreso"),
  formIngreso: document.getElementById("form-ingreso"),
  apiKey: document.getElementById("api-key"),
  errorIngreso: document.getElementById("error-ingreso"),
  // Marco
  app: document.getElementById("app"),
  estado: document.getElementById("estado"),
  nueva: document.getElementById("nueva"),
  historial: document.getElementById("historial"),
  verChat: document.getElementById("ver-chat"),
  verDocs: document.getElementById("ver-docs"),
  pantallaChat: document.getElementById("pantalla-chat"),
  pantallaDocs: document.getElementById("pantalla-docs"),
  // Chat
  mensajes: document.getElementById("mensajes"),
  pasos: document.getElementById("pasos"),
  form: document.getElementById("form-mensaje"),
  entrada: document.getElementById("entrada"),
  enviar: document.getElementById("enviar"),
  detener: document.getElementById("detener"),
  fuentes: document.getElementById("fuentes"),
  aprobacion: document.getElementById("aprobacion"),
  aprobacionMotivo: document.getElementById("aprobacion-motivo"),
  aprobacionCodigo: document.getElementById("aprobacion-codigo"),
  aprobar: document.getElementById("aprobar"),
  rechazar: document.getElementById("rechazar"),
  // Documentos
  soltar: document.getElementById("soltar"),
  archivo: document.getElementById("archivo"),
  tablaDocs: document.getElementById("tabla-docs"),
  listaDocs: document.getElementById("lista-docs"),
  sinDocs: document.getElementById("sin-docs"),
  errorDocs: document.getElementById("error-docs"),
};

let conversationId = null;
let runId = null;
let stream = null;
// Para retomar el stream donde quedó cuando el run se pausa y se reanuda.
let ultimoEventoId = 0;
let pendiente = null;
let destinoActual = null;
let sondeoDocs = null;

const PASOS = {
  retrieve_context: "Preparando contexto...",
  compact: "Resumiendo lo anterior...",
  agent: "Pensando...",
  tools: "Usando herramientas...",
  finalize: "Cerrando...",
};

// --- Utilidades ---

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

function nodo(etiqueta, clase, texto) {
  const n = document.createElement(etiqueta);
  if (clase) n.className = clase;
  if (texto !== undefined) n.textContent = texto;
  return n;
}

function tamanoLegible(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// --- Chat ---

function burbuja(rol, texto) {
  const li = nodo("li", rol === "user" ? "de-usuario" : "de-byte");
  const cuerpo = nodo("span", null, texto);
  li.append(cuerpo);
  el.mensajes.append(li);
  li.scrollIntoView({ block: "end" });
  return cuerpo;
}

/** Los tres puntitos mientras el agente todavía no escribió nada. */
function puntos(destino) {
  const cont = nodo("span", "escribiendo");
  cont.append(nodo("span"), nodo("span"), nodo("span"));
  destino.append(cont);
  return () => cont.remove();
}

function escuchar(eventsUrl, destino, desdeId = 0) {
  const url = desdeId > 0 ? `${eventsUrl}?last_event_id=${desdeId}` : eventsUrl;
  stream = new EventSource(url, { withCredentials: true });
  destinoActual = destino;
  let texto = destino.textContent || "";
  let quitarPuntos = texto ? null : puntos(destino);

  const on = (tipo, manejador) =>
    stream.addEventListener(tipo, (evento) => {
      // El id sirve para retomar sin repetir si el run se pausa.
      if (evento.lastEventId) ultimoEventoId = Number(evento.lastEventId);
      manejador(evento);
    });

  const escribir = (nuevo) => {
    if (quitarPuntos) {
      quitarPuntos();
      quitarPuntos = null;
    }
    texto = nuevo;
    destino.textContent = texto;
  };

  on("STEP_STARTED", (evento) => {
    const paso = JSON.parse(evento.data).stepName;
    el.pasos.textContent = PASOS[paso] || paso;
  });
  on("TOOL_CALL_START", (evento) => {
    el.pasos.textContent = `Usando ${JSON.parse(evento.data).toolCallName}...`;
  });
  on("TEXT_MESSAGE_CONTENT", (evento) => {
    escribir(texto + JSON.parse(evento.data).delta);
  });
  on("STATE_DELTA", (evento) => {
    const datos = JSON.parse(evento.data);
    if (datos.awaiting_approval) {
      mostrarAprobacion(datos.awaiting_approval);
    } else if ("awaiting_approval" in datos) {
      el.aprobacion.hidden = true;
      pendiente = null;
    }
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
      if (quitarPuntos) quitarPuntos();
      stream.close();
      stream = null;
      el.pasos.textContent = "Esperando tu confirmación...";
      return;
    }
    if (datos.status === "cancelled") {
      escribir(texto + (texto ? "\n" : "") + "[detenido]");
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
          if (mensaje?.content) escribir(mensaje.content);
        })
        .catch(() => {
          /* si falla, queda lo que llegó por el stream */
        });
    }
    if (quitarPuntos) quitarPuntos();
    terminar();
    cargarHistorial();
  });
  on("RUN_ERROR", (evento) => {
    const datos = JSON.parse(evento.data);
    escribir(texto + `\n[error: ${datos.message || datos.code}]`);
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
  el.entrada.style.height = "";
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

// --- Modo seguro ---

function mostrarAprobacion(datos) {
  pendiente = datos;
  const motivos = {
    web_y_codigo_en_el_mismo_run:
      "Esta conversación leyó contenido externo (web o documentos) y ahora Byte " +
      "quiere ejecutar código. Revisalo antes de aprobar.",
    modo_seguro_activado: "Pediste modo seguro: confirmá antes de ejecutar.",
  };
  el.aprobacionMotivo.textContent = motivos[datos.reason] || datos.reason || "";
  // Puede haber más de una ejecución en la misma tanda, y aprobar las alcanza a
  // todas: se muestran todas, o quien aprueba estaría decidiendo a ciegas.
  const codigos = datos.codes?.length ? datos.codes : [datos.code || "(sin código)"];
  el.aprobacionCodigo.textContent = codigos.join("\n\n---\n\n");
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

// --- Historial ---

/** "Hoy" / "Ayer" / "Esta semana" / fecha, como los agrupa el diseño. */
function grupoDe(iso) {
  const fecha = new Date(iso);
  const hoy = new Date();
  const dia = 86400000;
  const aMedianoche = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const dias = Math.round((aMedianoche(hoy) - aMedianoche(fecha)) / dia);
  if (dias <= 0) return "Hoy";
  if (dias === 1) return "Ayer";
  if (dias < 7) return "Esta semana";
  if (dias < 30) return "Este mes";
  return fecha.toLocaleDateString("es", { month: "long", year: "numeric" });
}

async function cargarHistorial() {
  let datos;
  try {
    datos = await pedir("/conversations?limit=50");
  } catch (_) {
    return; // el historial no es crítico: si falla, la conversación actual sigue
  }
  el.historial.replaceChildren();
  let grupoActual = null;
  let lista = null;

  for (const conv of datos.items) {
    const grupo = grupoDe(conv.updated_at);
    if (grupo !== grupoActual) {
      grupoActual = grupo;
      el.historial.append(nodo("p", "grupo-fecha", grupo));
      lista = nodo("ul", "conversaciones");
      el.historial.append(lista);
    }
    const li = document.createElement("li");
    if (conv.id === conversationId) li.setAttribute("aria-current", "true");
    const boton = nodo("button");
    boton.type = "button";
    boton.append(nodo("span", "conv-titulo", conv.title));
    if (conv.preview) boton.append(nodo("span", "conv-preview", conv.preview));
    boton.addEventListener("click", () => abrirConversacion(conv.id));
    li.append(boton);
    lista.append(li);
  }
}

async function abrirConversacion(id) {
  if (runId) return; // con un run en vuelo, cambiar de conversación deja el stream huérfano
  conversationId = id;
  el.mensajes.replaceChildren();
  el.fuentes.textContent = "";
  el.aprobacion.hidden = true;
  mostrarPantalla("chat");

  try {
    const detalle = await pedir(`/conversations/${id}`);
    for (const mensaje of detalle.messages) {
      // El marcador de "compactada hasta acá": lo de arriba ya no se le manda
      // al modelo, aunque se siga mostrando.
      if (detalle.summary_up_to_message_id && mensaje.id === detalle.summary_up_to_message_id) {
        el.mensajes.append(
          nodo("li", "compactado", "Byte resumió todo lo anterior para seguir la conversación")
        );
      }
      burbuja(mensaje.role === "user" ? "user" : "assistant", mensaje.content);
    }
  } catch (error) {
    el.pasos.textContent = "No se pudo abrir: " + error.message;
  }
  cargarHistorial();
}

async function nuevaConversacion() {
  if (runId) return;
  try {
    const conversacion = await pedir("/conversations", {
      method: "POST",
      body: JSON.stringify({}),
    });
    conversationId = conversacion.id;
    el.mensajes.replaceChildren();
    el.fuentes.textContent = "";
    el.aprobacion.hidden = true;
    mostrarPantalla("chat");
    el.entrada.focus();
    cargarHistorial();
  } catch (error) {
    el.pasos.textContent = "No se pudo crear: " + error.message;
  }
}

// --- Documentos ---

const ETIQUETAS = { indexed: "Indexado", processing: "Procesando", error: "Error" };

async function cargarDocumentos() {
  let datos;
  try {
    datos = await pedir("/documents");
  } catch (error) {
    // Sin Postgres no hay RAG: la API responde 503 y la pantalla lo dice.
    el.errorDocs.textContent = error.message;
    el.tablaDocs.hidden = true;
    el.sinDocs.hidden = true;
    return;
  }
  el.errorDocs.textContent = "";
  el.listaDocs.replaceChildren();
  el.tablaDocs.hidden = datos.items.length === 0;
  el.sinDocs.hidden = datos.items.length > 0;

  for (const doc of datos.items) {
    const tr = document.createElement("tr");
    const nombre = document.createElement("td");
    nombre.append(nodo("span", "nombre", doc.filename));
    if (doc.error_message) nombre.append(nodo("span", "motivo-error", doc.error_message));
    const estado = document.createElement("td");
    const etiqueta = nodo("span", "etiqueta", ETIQUETAS[doc.status] || doc.status);
    etiqueta.dataset.estado = doc.status;
    estado.append(etiqueta);
    const borrar = nodo("button", null, "Eliminar");
    borrar.type = "button";
    borrar.addEventListener("click", () => borrarDocumento(doc.id, doc.filename));
    const acciones = document.createElement("td");
    acciones.append(borrar);

    tr.append(
      nombre,
      estado,
      nodo("td", null, doc.status === "indexed" ? String(doc.chunks) : "—"),
      nodo("td", null, tamanoLegible(doc.size_bytes)),
      acciones
    );
    el.listaDocs.append(tr);
  }

  // Mientras haya alguno indexándose se refresca solo: la ingesta es asíncrona
  // y no hay eventos, así que la única forma de ver el cambio es preguntar.
  const enCurso = datos.items.some((d) => d.status === "processing");
  clearTimeout(sondeoDocs);
  if (enCurso && !el.pantallaDocs.hidden) sondeoDocs = setTimeout(cargarDocumentos, 2000);
}

async function subir(archivo) {
  if (!archivo) return;
  el.errorDocs.textContent = "";
  const cuerpo = new FormData();
  cuerpo.append("file", archivo);
  try {
    // Sin Content-Type a mano: el navegador pone el boundary del multipart.
    const respuesta = await fetch(`${API}/documents`, {
      method: "POST",
      credentials: "same-origin",
      body: cuerpo,
    });
    if (!respuesta.ok) {
      const detalle = await respuesta.json().catch(() => null);
      throw new Error(detalle?.error?.message || respuesta.statusText);
    }
  } catch (error) {
    el.errorDocs.textContent = "No se pudo subir: " + error.message;
  }
  cargarDocumentos();
}

async function borrarDocumento(id, nombre) {
  if (!confirm(`¿Eliminar "${nombre}"? Byte deja de poder consultarlo.`)) return;
  try {
    await pedir(`/documents/${id}`, { method: "DELETE" });
  } catch (error) {
    el.errorDocs.textContent = "No se pudo eliminar: " + error.message;
  }
  cargarDocumentos();
}

// --- Navegación y arranque ---

function mostrarPantalla(cual) {
  const enChat = cual === "chat";
  el.pantallaChat.hidden = !enChat;
  el.pantallaDocs.hidden = enChat;
  el.verChat.setAttribute("aria-current", enChat ? "page" : "false");
  el.verDocs.setAttribute("aria-current", enChat ? "false" : "page");
  if (enChat) {
    clearTimeout(sondeoDocs);
  } else {
    cargarDocumentos();
  }
}

async function mostrarEstado() {
  try {
    const salud = await pedir("/health/details");
    el.estado.dataset.salud = salud.status;
    el.estado.textContent =
      salud.status === "ok"
        ? `En línea, corriendo local · ${salud.model}`
        : `Degradado · ollama: ${salud.ollama}, db: ${salud.db}`;
  } catch (_) {
    el.estado.dataset.salud = "";
    el.estado.textContent = "Sin conexión";
  }
}

async function entrar(evento) {
  evento.preventDefault();
  el.errorIngreso.textContent = "";
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
    el.ingreso.hidden = true;
    el.app.hidden = false;
    el.entrada.focus();
    mostrarEstado();
    cargarHistorial();
  } catch (error) {
    el.errorIngreso.textContent = "No se pudo entrar: " + error.message;
  }
}

el.formIngreso.addEventListener("submit", entrar);
el.form.addEventListener("submit", enviar);
el.detener.addEventListener("click", detener);
el.aprobar.addEventListener("click", () => decidir(true));
el.rechazar.addEventListener("click", () => decidir(false));
el.nueva.addEventListener("click", nuevaConversacion);
el.verChat.addEventListener("click", () => mostrarPantalla("chat"));
el.verDocs.addEventListener("click", () => mostrarPantalla("docs"));

el.entrada.addEventListener("keydown", (e) => {
  // Enter envía; Shift+Enter hace salto de línea, como en cualquier chat.
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    el.form.requestSubmit();
  }
});
el.entrada.addEventListener("input", () => {
  // El textarea crece con el texto hasta el tope que pone el CSS.
  el.entrada.style.height = "auto";
  el.entrada.style.height = `${el.entrada.scrollHeight}px`;
});

el.soltar.addEventListener("click", () => el.archivo.click());
el.archivo.addEventListener("change", () => {
  subir(el.archivo.files[0]);
  el.archivo.value = "";
});
for (const tipo of ["dragenter", "dragover"]) {
  el.soltar.addEventListener(tipo, (e) => {
    e.preventDefault();
    el.soltar.dataset.encima = "true";
  });
}
for (const tipo of ["dragleave", "drop"]) {
  el.soltar.addEventListener(tipo, (e) => {
    e.preventDefault();
    el.soltar.dataset.encima = "false";
  });
}
el.soltar.addEventListener("drop", (e) => subir(e.dataTransfer?.files?.[0]));

// El estado del backend se refresca solo: si Ollama se cae, el punto lo muestra.
setInterval(mostrarEstado, 30000);
