// La página de ofertas: pegás, y el servidor dice a cuáles aplicar.
//
// Todo el cálculo pasa en el servidor a propósito. El puntaje sale del mismo
// criterio que usa el cazador y que está en `perfil/busqueda.toml`; tenerlo
// también acá serían dos criterios que se desincronizan y nadie se entera.
//
// **Sólo se dibujan las elegidas.** Las descartadas ni siquiera viajan: ver
// `a_json` en `empleo/pegado.py`. Lo que sí viaja es cuántas se leyeron, para
// que "ninguna sirve" no se lea igual que "no entendí lo que pegaste".
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
const boton = document.getElementById("boton-analizar");
const error = document.getElementById("error");
const resumen = document.getElementById("resumen");
const resultados = document.getElementById("resultados");

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

function plata(monto) {
  // "$300K" y no "$300,000": en una línea de datos el orden de magnitud es lo
  // que se compara, y los ceros sólo ocupan lugar.
  if (monto === null || monto === undefined) return "";
  if (monto >= 1000) return `$${Math.round(monto / 1000)}K gastados`;
  return monto > 0 ? `$${monto} gastados` : "sin historial de gasto";
}

// Los nombres de las señales son claves del TOML —`largo_plazo`, `solo_us`— y
// se ven como código en una línea que un humano lee de reojo. Acá se traducen.
// Una señal sin traducir cae al guión bajo cambiado por espacio: es feo pero se
// entiende, que es mejor que esconderla por no tener entrada en la tabla.
const NOMBRES = {
  largo_plazo: "largo plazo",
  cliente_norteamerica: "cliente de EE.UU./Canadá",
  pide_experto: "piden experto",
  duracion_larga: "duración larga",
  duracion_corta: "duración corta",
  jornada_completa: "jornada completa",
  // Se nombra el hecho, no el veredicto. Si una de estas asoma igual es porque
  // ganó en todo lo demás, y ahí lo que hace falta saber es de dónde es.
  cliente_bloqueado: "⚠ cliente fuera de tu lista",
  sin_largo_plazo: "sin continuidad",
  remoto_global: "remoto global",
  solo_us: "sólo EE.UU.",
  sin_patrocinio: "no patrocina visa",
  patrocinio: "patrocina visa",
};

function senal(nombre) {
  return NOMBRES[nombre] || nombre.replace(/_/g, " ");
}

function datosDe(oferta) {
  const partes = [];
  if (oferta.empresa) partes.push(oferta.empresa);
  if (oferta.gastado !== null && oferta.gastado !== undefined) partes.push(plata(oferta.gastado));
  if (oferta.verificado === true) partes.push("pago verificado");
  if (oferta.verificado === false) partes.push("pago SIN verificar");
  if (oferta.competencia !== null && oferta.competencia !== undefined) {
    partes.push(`${oferta.competencia} compitiendo`);
  }
  if (oferta.horas !== null && oferta.horas !== undefined) {
    partes.push(
      oferta.horas < 48
        ? `hace ${Math.round(oferta.horas)} h`
        : `hace ${Math.round(oferta.horas / 24)} d`
    );
  }
  if (oferta.senales.length) partes.push(oferta.senales.map(senal).join(", "));
  return partes.join(" · ");
}

function pintar(datos) {
  resultados.replaceChildren();
  resumen.hidden = false;
  resumen.classList.toggle("parcial", datos.poca_informacion);

  const elegidas = datos.ofertas.length;
  resumen.replaceChildren(
    texto(crear("p"), `${elegidas} de ${datos.analizadas} ofertas`)
  );

  // Tres finales distintos, y confundirlos es el error caro de esta página.
  if (!datos.analizadas) {
    resumen.hidden = true;
    resultados.appendChild(
      texto(crear("p", "datos"), "No reconocí ninguna oferta. ¿Pegaste la lista de resultados?")
    );
    return;
  }

  if (datos.poca_informacion) {
    resultados.appendChild(
      texto(
        crear("p", "datos"),
        "Estas tarjetas no traen la descripción del puesto, así que no puedo elegir: " +
          "el puntaje saldría de un título de seis palabras. Pegá la búsqueda con las " +
          "descripciones incluidas."
      )
    );
    return;
  }

  if (!elegidas) {
    // Que no haya ninguna es un resultado, no una falla. Decirlo evita que se
    // lea como "la página no anduvo" y se vuelva a pegar lo mismo.
    resultados.appendChild(
      texto(
        crear("p", "datos"),
        `Leí ${datos.analizadas} y ninguna llega al mínimo. Hoy no hay a cuál aplicar.`
      )
    );
    return;
  }

  datos.ofertas.forEach((oferta, i) => {
    // El puesto en la lista, no sólo el puntaje: "1" se lee de un vistazo y el
    // puntaje es una escala sin tope que no dice nada por sí sola.
    const fila = crear("article", `oferta aplicar${i === 0 ? " mejor" : ""}`);
    const cabecera = crear("div", "oferta-cabecera");
    cabecera.appendChild(texto(crear("span", "puesto"), `#${i + 1}`));
    cabecera.appendChild(texto(crear("span", "puntaje"), String(oferta.puntaje)));

    // El título, en un botón que lo copia.
    //
    // La pantalla de búsqueda no trae el enlace de cada oferta —Upwork lo pone
    // en el href del título y al copiar la página se pierde—, así que para
    // abrirla hay que buscarla por su nombre. Copiarlo a mano de una lista es
    // justo el paso donde se abandona: se selecciona de más, se pega con el
    // puntaje pegado adelante y la búsqueda no encuentra nada.
    const titulo = crear("button", "titulo", oferta.titulo);
    titulo.type = "button";
    titulo.title = "Copiar el título para buscarlo en Upwork";
    titulo.addEventListener("click", async () => {
      const antes = titulo.textContent;
      try {
        await navigator.clipboard.writeText(oferta.titulo);
        titulo.textContent = "✓ copiado — pegalo en la búsqueda de Upwork";
      } catch {
        // Sin permiso de portapapeles —pasa en http:// y en algunos
        // navegadores— se selecciona el texto para que copiar sea un Ctrl-C.
        const rango = document.createRange();
        rango.selectNodeContents(titulo);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(rango);
        titulo.textContent = antes;
      }
      setTimeout(() => (titulo.textContent = antes), 2500);
    });
    cabecera.appendChild(titulo);
    fila.appendChild(cabecera);
    const datosTexto = datosDe(oferta);
    if (datosTexto) fila.appendChild(texto(crear("p", "datos"), datosTexto));
    fila.appendChild(texto(crear("p", "motivos"), oferta.motivos.join("; ")));
    resultados.appendChild(fila);
  });
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
    const cuerpo = JSON.stringify({ texto: contenido });
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
