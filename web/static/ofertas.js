// La página de ofertas: pegás, y el servidor dice a cuáles aplicar.
//
// Todo el cálculo pasa en el servidor a propósito. El puntaje sale del mismo
// criterio que usa el cazador y que está en `perfil/busqueda.toml`; tenerlo
// también acá serían dos criterios que se desincronizan y nadie se entera.
const API = "/api/v1";

// La clave sólo hace falta cuando la página está desplegada: en la Mac, la API
// grande autentica con la cookie que ya dejó la pantalla principal. Se guarda en
// sessionStorage y no en localStorage a propósito — se borra al cerrar la
// pestaña, que para una URL pública en un teléfono prestado es la diferencia.
const CLAVE = "byte-ofertas-clave";

function clave() {
  try {
    return sessionStorage.getItem(CLAVE) || "";
  } catch {
    return "";
  }
}

function guardarClave(valor) {
  try {
    sessionStorage.setItem(CLAVE, valor);
  } catch {
    /* Modo privado: se pedirá de nuevo, que es molesto pero no roto. */
  }
}

function pedirClave() {
  const valor = window.prompt("Clave de acceso (OFERTAS_CLAVE)");
  if (valor) guardarClave(valor.trim());
  return Boolean(valor);
}

const form = document.getElementById("form-pegado");
const pegado = document.getElementById("pegado");
const connects = document.getElementById("connects");
const boton = document.getElementById("boton-analizar");
const error = document.getElementById("error");
const resumen = document.getElementById("resumen");
const resultados = document.getElementById("resultados");
const guia = document.getElementById("guia");
const alertas = document.getElementById("alertas");

const SITIOS = { upwork: "Upwork", linkedin: "LinkedIn", generico: "sitio no reconocido" };

function texto(elemento, contenido) {
  // textContent y no innerHTML: lo pegado viene de una página de un tercero.
  elemento.textContent = contenido;
  return elemento;
}

function crear(etiqueta, clase, contenido) {
  const nodo = document.createElement(etiqueta);
  if (clase) nodo.className = clase;
  if (contenido !== undefined) nodo.textContent = contenido;
  return nodo;
}

function datosDe(oferta) {
  const partes = [];
  if (oferta.empresa) partes.push(oferta.empresa);
  if (oferta.competencia !== null && oferta.competencia !== undefined) {
    partes.push(`${oferta.competencia} compitiendo`);
  }
  if (oferta.horas !== null && oferta.horas !== undefined) {
    partes.push(oferta.horas < 48 ? `hace ${Math.round(oferta.horas)} h`
                                  : `hace ${Math.round(oferta.horas / 24)} d`);
  }
  if (oferta.verificado === false) partes.push("pago SIN verificar");
  if (oferta.senales.length) partes.push(oferta.senales.join(", "));
  return partes.join(" · ");
}

// --- Alertas guardadas ---
//
// Pegar resultados es para hoy; una alerta guardada trabaja sola todos los días.
// Para LinkedIn y Upwork, que no se pueden leer por API, es la única forma de
// enterarse temprano — y temprano es casi todo: en Upwork las primeras
// propuestas se leen y las que llegan con veinte encima, no.
//
// Cada plataforma entiende una sintaxis distinta, así que el servidor manda una
// cadena por plataforma y acá no se arma ninguna.
function bloqueDeAlerta(alerta) {
  const caja = crear("article", "alerta");
  caja.appendChild(texto(crear("h3"), alerta.plataforma));

  const fila = crear("div", "alerta-consulta");
  const consulta = texto(crear("code"), alerta.consulta);
  const boton = crear("button", "copiar", "Copiar");
  boton.type = "button";
  boton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(alerta.consulta);
      boton.textContent = "Copiado";
    } catch {
      // Sin permiso de portapapeles —pasa en http:// y en algunos navegadores—
      // se selecciona el texto para que copiarlo sea un Ctrl-C.
      const rango = document.createRange();
      rango.selectNodeContents(consulta);
      const seleccion = window.getSelection();
      seleccion.removeAllRanges();
      seleccion.addRange(rango);
      boton.textContent = "Copiá con Ctrl-C";
    }
    setTimeout(() => (boton.textContent = "Copiar"), 2500);
  });
  fila.appendChild(consulta);
  fila.appendChild(boton);
  caja.appendChild(fila);

  caja.appendChild(texto(crear("p", "donde"), alerta.donde));
  caja.appendChild(texto(crear("p", "nota"), alerta.nota));
  return caja;
}

async function cargarAlertas() {
  const cabeceras = {};
  if (clave()) cabeceras.Authorization = `Bearer ${clave()}`;
  let respuesta;
  try {
    respuesta = await fetch(`${API}/ofertas/alertas`, {
      credentials: "same-origin",
      headers: cabeceras,
    });
  } catch {
    return; // Sin red no se muestra la sección; no es un error que valga interrumpir.
  }
  // Un 401 acá no interrumpe con un prompt: la página recién se abrió y
  // preguntar la clave antes de que hagas nada es molesto. Pero tampoco puede
  // desaparecer en silencio — así es como esta sección quedó invisible en el
  // primer despliegue: sin clave en sessionStorage, el fetch daba 401 y la
  // sección no se dibujaba nunca, que es justo cuando hace falta. Se ofrece un
  // botón: un clic en vez de una pregunta.
  if (respuesta.status === 401) {
    alertas.replaceChildren();
    alertas.hidden = false;
    alertas.appendChild(texto(crear("h2"), "Alertas guardadas"));
    const boton = crear("button", "copiar", "Ver qué pegar en cada plataforma");
    boton.type = "button";
    boton.addEventListener("click", () => {
      if (pedirClave()) cargarAlertas();
    });
    alertas.appendChild(boton);
    return;
  }
  if (!respuesta.ok) return;
  const datos = await respuesta.json();
  if (!datos.alertas || !datos.alertas.length) return;
  alertas.replaceChildren();
  alertas.hidden = false;
  alertas.appendChild(texto(crear("h2"), "Alertas guardadas: que te avisen ellos"));
  alertas.appendChild(
    texto(
      crear("p", "limite"),
      "Esto se hace una vez por plataforma. Los términos salen del mismo " +
      "criterio que puntúa las ofertas, así que las alertas no te van a traer " +
      "cosas que después el puntaje hunda."
    )
  );
  for (const alerta of datos.alertas) alertas.appendChild(bloqueDeAlerta(alerta));
}

function lista(clase, titulo, entradas) {
  // Una <ol> y no un párrafo: son pasos, y se leen en orden.
  const seccion = crear("section", clase);
  seccion.appendChild(texto(crear("h3"), titulo));
  const ol = crear("ol");
  for (const entrada of entradas) ol.appendChild(texto(crear("li"), entrada));
  seccion.appendChild(ol);
  return seccion;
}

function pintarGuia(datos) {
  // Lo que se hace en Upwork no es lo que se hace en LinkedIn: en uno postular
  // cuesta Connects y en el otro es gratis. Por eso la lista sale del sitio que
  // el servidor detectó, y no hay una lista sola para los dos.
  guia.replaceChildren();
  if (!datos) {
    guia.hidden = true;
    return;
  }
  guia.hidden = false;
  guia.appendChild(texto(crear("h2"), `Si es ${datos.nombre}`));
  guia.appendChild(texto(crear("p", "limite"), datos.limite));
  if (datos.medidas.length) {
    guia.appendChild(lista("medidas", "Lo que pegaste, en números", datos.medidas));
  }
  if (datos.filtros.length) {
    guia.appendChild(lista("filtros", "Filtros a poner en el sitio", datos.filtros));
  }
  if (datos.pasos.length) {
    guia.appendChild(lista("pasos", "Cómo aplicar acá", datos.pasos));
  }
}

function pintar(datos) {
  resultados.replaceChildren();
  pintarGuia(datos.guia);
  resumen.hidden = false;
  resumen.classList.toggle("parcial", datos.poca_informacion);

  const lineas = [
    `${datos.ofertas.length} ofertas · ${SITIOS[datos.sitio] || datos.sitio}`,
  ];
  if (datos.descartados) lineas.push(`${datos.descartados} bloques descartados por no ser ofertas`);
  if (datos.sitio === "upwork" && datos.connects_disponibles) {
    lineas.push(
      `${datos.connects_gastados} de ${datos.connects_disponibles} connects · ` +
      `quedan ${datos.connects_disponibles - datos.connects_gastados}`
    );
  }
  resumen.replaceChildren(texto(crear("p"), lineas.join(" · ")));

  if (datos.poca_informacion) {
    resumen.appendChild(
      texto(
        crear("p"),
        "Estas tarjetas no traen la descripción del puesto, así que el orden sirve " +
        "—competencia y frescura son datos duros— pero no alcanza para decir a cuál " +
        "aplicar. Abrí las de arriba y pegá una sola completa para eso."
      )
    );
  }

  if (!datos.ofertas.length) {
    resultados.appendChild(
      texto(crear("p", "datos"), "No reconocí ninguna oferta. ¿Pegaste la lista de resultados?")
    );
    return;
  }

  for (const oferta of datos.ofertas) {
    const fila = crear("article", `oferta${oferta.aplicar ? " aplicar" : ""}`);
    const cabecera = crear("div", "oferta-cabecera");
    cabecera.appendChild(texto(crear("span", "puntaje"), String(oferta.puntaje)));
    cabecera.appendChild(texto(crear("strong"), oferta.titulo));
    if (oferta.aplicar) {
      const etiqueta = oferta.connects ? `aplicar · ${oferta.connects} connects` : "aplicar";
      cabecera.appendChild(texto(crear("span", "veredicto si"), etiqueta));
    }
    fila.appendChild(cabecera);
    const datosTexto = datosDe(oferta);
    if (datosTexto) fila.appendChild(texto(crear("p", "datos"), datosTexto));
    fila.appendChild(texto(crear("p", "motivos"), oferta.motivos.join("; ")));
    resultados.appendChild(fila);
  }
}

form.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  error.textContent = "";
  const contenido = pegado.value.trim();
  if (!contenido) {
    error.textContent = "Pegá primero la página de resultados.";
    return;
  }
  boton.disabled = true;
  boton.textContent = "Analizando…";
  try {
    const cuerpo = JSON.stringify({
      texto: contenido,
      connects: Number(connects.value) || 0,
    });
    const cabeceras = { "Content-Type": "application/json" };
    if (clave()) cabeceras.Authorization = `Bearer ${clave()}`;

    let respuesta = await fetch(`${API}/ofertas/pegado`, {
      method: "POST",
      credentials: "same-origin",
      headers: cabeceras,
      body: cuerpo,
    });

    // Un 401 puede ser dos cosas distintas: la cookie de la API grande venció, o
    // esto está desplegado y falta la clave. Se pide una vez y se reintenta; si
    // vuelve a fallar, ahí sí es la cookie.
    if (respuesta.status === 401 && pedirClave()) {
      respuesta = await fetch(`${API}/ofertas/pegado`, {
        method: "POST",
        credentials: "same-origin",
        headers: { ...cabeceras, Authorization: `Bearer ${clave()}` },
        body: cuerpo,
      });
    }
    if (respuesta.status === 401) {
      error.textContent =
        "Sin acceso. Si estás en la Mac, entrá desde la página principal; " +
        "si es la versión desplegada, revisá la clave.";
      return;
    }
    if (!respuesta.ok) {
      error.textContent = `No se pudo analizar (${respuesta.status}).`;
      return;
    }
    pintar(await respuesta.json());
    // Ya hay clave: si la sección de alertas se había quedado con el botón, ahora
    // se llena sola. No hace falta que la pidas dos veces.
    if (alertas.querySelector("button")) cargarAlertas();
  } catch {
    error.textContent = "No se pudo hablar con Byte. ¿Está corriendo?";
  } finally {
    boton.disabled = false;
    boton.textContent = "Analizar";
  }
});

cargarAlertas();
