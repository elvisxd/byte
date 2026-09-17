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

function pintar(datos) {
  resultados.replaceChildren();
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
  } catch {
    error.textContent = "No se pudo hablar con Byte. ¿Está corriendo?";
  } finally {
    boton.disabled = false;
    boton.textContent = "Analizar";
  }
});
