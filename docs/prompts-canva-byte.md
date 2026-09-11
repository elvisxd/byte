# Prompts para Canva — Proyecto "Byte" (agente de IA local)

## Identidad visual (usar en TODOS los prompts)
Pegar este bloque al inicio de cada prompt para que Canva mantenga coherencia:

> Identidad de marca "Byte": asistente de IA que corre localmente, para desarrolladores. Mascota: perrito geométrico y amigable, estilo flat (sin degradados ni sombras), pelaje azul marino (#3B4A6B), hocico crema (#F4EDE4), con una etiqueta redonda ámbar (#F5A524) en el collar que dice `</>`. Paleta: azul marino #3B4A6B, azul oscuro #2E3A57, crema #F4EDE4, ámbar #F5A524, casi negro #1A1F2E. Tipografía sans-serif moderna y limpia (tipo Inter o Manrope), títulos en peso medio, nunca en mayúsculas completas. Estética: minimalista, profesional, tecnológica, mucho espacio en blanco, bordes redondeados suaves. Nada de degradados, brillos ni efectos neón.

---

## 1. Pantalla de chat (diapositiva / mockup)
> [Identidad de marca] Mockup profesional de una interfaz web de chat con un asistente de IA, vista de escritorio. Header superior con el logo del perrito a la izquierda, el nombre "Byte" y un punto verde con el texto "En línea, corriendo local". Área central con burbujas de conversación: las del usuario a la derecha en ámbar suave, las del agente a la izquierda en crema. Incluir un indicador de "escribiendo" con tres puntitos. Barra inferior con campo de texto "Escribí un mensaje" y un botón circular azul marino con flecha para enviar. Fondo blanco, bordes redondeados, estilo flat minimalista.

## 2. Historial de conversaciones
> [Identidad de marca] Mockup profesional de una barra lateral (sidebar) de una app web de IA, vista de escritorio. Arriba, el logo del perrito y el nombre "Byte". Debajo, un botón destacado en ámbar "Nueva conversación". Luego un buscador con placeholder "Buscar conversaciones". Debajo, una lista de conversaciones anteriores agrupadas por fecha ("Hoy", "Ayer", "Esta semana"), cada una con un título corto y una vista previa en gris. La conversación activa resaltada con fondo crema. Fondo blanco, estilo flat, limpio y fácil de escanear.

## 3. Carga de documentos para RAG
> [Identidad de marca] Mockup profesional de una pantalla web para subir documentos a un asistente de IA, vista de escritorio. Título "Tus documentos". Una zona grande de arrastrar y soltar con borde punteado azul marino, un ícono de carpeta y el texto "Arrastrá tus archivos acá o hacé clic para subir. PDF, TXT, MD". Debajo, una tabla o lista de documentos ya indexados con nombre, tamaño, fecha, y un estado en forma de etiqueta: "Indexado" en verde, "Procesando" en ámbar con barra de progreso. Cada fila con acciones de eliminar y re-indexar. Fondo blanco, estilo flat minimalista.

## 3b. Pantalla del CLI en Go (diapositiva)
> [Identidad de marca] Mockup de una ventana de terminal oscura (fondo casi negro #1A1F2E) de una herramienta de línea de comandos llamada "byte". Barra de título con un pequeño ícono del perrito. Prompt `byte>` en ámbar. Ejemplos de comandos: `byte ask "cómo creo un endpoint en FastAPI"`, `byte search "última versión de LangGraph"`, `byte run script.py`. Respuestas del agente en texto crema, con líneas de estado en gris como "Buscando en la web…", "Ejecutando código…", "Listo". Barra inferior con atajos: Ctrl+C salir, Tab autocompletar, flecha arriba historial. Tipografía monoespaciada, estilo flat, sin degradados.

## 4. Logo definitivo (design_type: logo)
> [Identidad de marca] Logo del perrito "Byte" en versión definitiva: cabeza de perro geométrica y simétrica, orejas triangulares, ojos redondos con un brillo, hocico crema, collar azul oscuro con una etiqueta circular ámbar que dice `</>`. Versión principal con el nombre "Byte" debajo en sans-serif peso medio. Incluir variante solo ícono (sin texto) para usar como favicon y avatar de GitHub. Fondo transparente o blanco. Estilo flat, sin degradados.

## 5. Banner para el README de GitHub (design_type: youtube_banner o facebook_cover)
> [Identidad de marca] Banner horizontal para la portada de un repositorio de GitHub. A la izquierda el perrito Byte, a la derecha el título "Byte" grande y debajo el subtítulo "Agente de IA local. Programa, busca en internet y usa herramientas, sin depender de APIs pagas". Debajo, tres etiquetas pequeñas con bordes redondeados: "Python · FastAPI", "Ollama · Qwen3-Coder", "MCP · LangGraph". Fondo blanco o crema muy claro, mucho espacio en blanco, estilo flat.

## 6. Post para LinkedIn presentando el proyecto (design_type: instagram_post o twitter_post)
> [Identidad de marca] Post cuadrado para redes presentando un proyecto de portafolio. Arriba el perrito Byte. Título: "Construí mi propio agente de IA que corre 100% local". Tres puntos cortos: "Modelo open source (Qwen3-Coder) sin APIs pagas", "Busca en internet, programa y usa herramientas vía MCP", "Desplegado en Railway con costo mínimo". Abajo pequeño: "Python · FastAPI · LangGraph · Ollama". Fondo azul marino con texto en crema, o fondo crema con texto en azul marino. Estilo flat, profesional.

---

## Cómo usarlos
- Para las **pantallas 1, 2, 3 y 3b**: van como presentación (una diapositiva por pantalla). En Canva, el flujo es: revisar el esquema → aprobar → generar. Ya generadas y guardadas: pantallas 1-3 en una presentación, CLI en otra.
- Para el **logo, banner y post**: van como diseños individuales, cada uno con su `design_type` indicado.
- Dos errores distintos de Canva, dos soluciones opuestas:
  - "Common queries will not be generated" → el prompt es demasiado genérico: **agregar** detalle.
  - "Design generation failed" → el prompt es demasiado largo o tiene caracteres raros: **acortar** la descripción y sacar códigos hex, símbolos como `</>` y puntuación. Fue lo que pasó en la práctica: falló con descripciones largas y funcionó al acortarlas.
- Siempre pegar el bloque de identidad de marca al inicio para que todas las piezas se vean de la misma familia.
