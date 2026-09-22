/* Alejandria — JavaScript de andar por casa: sin dependencias, sin compilar.
   Solo cuatro cosas: selección + teclado, refresco del panel de detalle por
   AJAX, los campos personalizados de la ficha y la ventana de subida. Todo lo
   demás lo pinta Django. */
(function () {
  "use strict";

  const $ = (s, raiz = document) => raiz.querySelector(s);
  const $$ = (s, raiz = document) => Array.from(raiz.querySelectorAll(s));
  const csrf = () => (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || "";

  /* --- Tema ---------------------------------------------------------------- */
  const botonTema = $("#boton-tema");
  if (botonTema) {
    botonTema.addEventListener("click", () => {
      const ahora = document.documentElement.dataset.tema;
      const nuevo = ahora === "oscuro" ? "claro" : "oscuro";
      document.documentElement.dataset.tema = nuevo;
      document.cookie = `tema=${nuevo};path=/;max-age=31536000`;
    });
  }

  /* --- Anchos de los paneles (se recuerdan en el navegador) ----------------- */
  const raiz = document.documentElement;
  ["lateral", "detalle"].forEach((cual) => {
    const guardado = localStorage.getItem(`alejandria-${cual}`);
    if (guardado) raiz.style.setProperty(`--${cual}`, guardado);
  });
  $$(".tirador").forEach((tirador) => {
    tirador.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const cual = tirador.dataset.tira;
      const inicio = e.clientX;
      const ancho = parseInt(getComputedStyle(raiz).getPropertyValue(`--${cual}`), 10);
      const mover = (ev) => {
        const delta = cual === "lateral" ? ev.clientX - inicio : inicio - ev.clientX;
        const valor = Math.min(640, Math.max(180, ancho + delta));
        raiz.style.setProperty(`--${cual}`, valor + "px");
      };
      const soltar = () => {
        document.removeEventListener("mousemove", mover);
        document.removeEventListener("mouseup", soltar);
        localStorage.setItem(`alejandria-${cual}`, getComputedStyle(raiz).getPropertyValue(`--${cual}`));
        document.body.style.userSelect = "";
      };
      document.body.style.userSelect = "none";
      document.addEventListener("mousemove", mover);
      document.addEventListener("mouseup", soltar);
    });
  });

  /* --- Barra lateral: filtrar listas largas y recordar secciones ------------- */
  $$(".filtro-lista").forEach((caja) => {
    const lista = $(caja.dataset.filtra);
    if (!lista) return;
    caja.addEventListener("input", () => {
      const texto = caja.value.trim().toLowerCase();
      $$("[data-nombre]", lista).forEach((fila) => {
        fila.style.display = !texto || fila.dataset.nombre.includes(texto) ? "" : "none";
      });
    });
  });

  $$("details.grupo").forEach((seccion, i) => {
    const clave = `alejandria-seccion-${seccion.id || i}`;
    const guardado = localStorage.getItem(clave);
    if (guardado !== null) seccion.open = guardado === "1";
    seccion.addEventListener("toggle", () => {
      localStorage.setItem(clave, seccion.open ? "1" : "0");
    });
  });

  /* --- Selección de documentos y panel de detalle --------------------------- */
  const contenedor = $("#documentos");
  const detalle = $("#detalle");

  function filas() {
    return $$("#documentos [data-id]");
  }

  function marcarActiva(fila) {
    filas().forEach((f) => f.classList.toggle("activa", f === fila));
  }

  let peticionEnCurso = null;
  function abrir(fila, empujarUrl = true) {
    if (!fila || !detalle) return;
    marcarActiva(fila);
    const id = fila.dataset.id;
    if (peticionEnCurso) peticionEnCurso.abort();
    peticionEnCurso = new AbortController();
    fetch(`/doc/${id}/panel/`, { signal: peticionEnCurso.signal })
      .then((r) => r.text())
      .then((html) => {
        detalle.innerHTML = html;
        detalle.scrollTop = 0;
        engancharFicha();
      })
      .catch(() => {});
    if (empujarUrl) {
      const url = new URL(location.href);
      url.searchParams.set("doc", id);
      history.replaceState(null, "", url);
    }
  }

  if (contenedor) {
    contenedor.addEventListener("click", (e) => {
      if (e.target.matches("input,button,a,label")) return;
      const fila = e.target.closest("[data-id]");
      if (fila) abrir(fila);
    });
    contenedor.addEventListener("dblclick", (e) => {
      const fila = e.target.closest("[data-id]");
      if (fila) window.open(`/doc/${fila.dataset.id}/fichero/`, "_blank");
    });
  }

  /* --- Teclado: j/k para moverse, Enter abre, / busca ----------------------- */
  document.addEventListener("keydown", (e) => {
    const dialogo = $("#dialogo-subida");
    if (dialogo && dialogo.open) return;  // la ventana de subida manda mientras esté abierta
    const escribiendo = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (e.key === "/" && !escribiendo) {
      e.preventDefault();
      $("#q") && $("#q").focus();
      return;
    }
    if (e.key === "Escape" && escribiendo) document.activeElement.blur();
    if (escribiendo || !contenedor) return;
    const lista = filas();
    if (!lista.length) return;
    const actual = lista.findIndex((f) => f.classList.contains("activa"));
    if (e.key === "j" || e.key === "ArrowDown") {
      e.preventDefault();
      const siguiente = lista[Math.min(lista.length - 1, actual + 1)];
      siguiente.scrollIntoView({ block: "nearest" });
      abrir(siguiente);
    } else if (e.key === "k" || e.key === "ArrowUp") {
      e.preventDefault();
      const anterior = lista[Math.max(0, actual - 1)];
      anterior.scrollIntoView({ block: "nearest" });
      abrir(anterior);
    } else if (e.key === "Enter" && actual >= 0) {
      window.open(`/doc/${lista[actual].dataset.id}/fichero/`, "_blank");
    }
  });

  /* --- Campos personalizados de la ficha ------------------------------------ */
  /* Se eligen uno o varios de la lista y aparecen en el acto, ya listos para
     escribir. Antes había que elegir uno, darle a Guardar y esperar a que el
     servidor lo pintara, que es lo que despistaba. */
  const CONTROL = { fecha: "date", numero: "number", moneda: "number", url: "url" };

  function filaDeCampo(pk, nombre, tipo, opciones) {
    const fila = document.createElement("div");
    fila.className = "campo-fila nueva";
    fila.dataset.campo = pk;

    const etiqueta = document.createElement("label");
    etiqueta.className = "campo";
    etiqueta.append(nombre);

    let control;
    if (tipo === "booleano" || tipo === "seleccion") {
      control = document.createElement("select");
      control.append(new Option("—", ""));
      const valores = tipo === "booleano" ? [["1", "Sí"], ["0", "No"]] : opciones.map((o) => [o, o]);
      valores.forEach(([valor, texto]) => control.append(new Option(texto, valor)));
    } else {
      control = document.createElement("input");
      control.type = CONTROL[tipo] || "text";
      if (tipo === "moneda") control.step = "0.01";
    }
    control.name = `campo_${pk}`;
    etiqueta.append(control);

    // Marca para que el servidor lo conserve aunque se guarde vacío: si no,
    // añadir un campo hoy y rellenarlo mañana sería imposible.
    const mantener = document.createElement("input");
    mantener.type = "hidden";
    mantener.name = "campo_mantener";
    mantener.value = pk;

    const quitar = document.createElement("button");
    quitar.type = "button";
    quitar.className = "quitar-campo";
    quitar.title = "Quitar este campo del documento";
    quitar.textContent = "×";

    fila.append(etiqueta, mantener, quitar);
    return { fila, control };
  }

  function engancharCampos() {
    const lista = $("#campos-lista");
    const bloque = $("#anadir-campo");
    const boton = $("#boton-anadir-campo");
    const elegibles = $("#campos-elegibles");
    const vacio = $("#campos-vacio");
    const filtro = $("#filtro-campos");
    const botonNuevo = $("#boton-campo-nuevo");
    const campoNuevo = $("#campo-nuevo");
    if (!lista) return;

    const revisarVacio = () => {
      if (vacio) vacio.hidden = !!lista.querySelector(".campo-fila:not([hidden])");
    };

    if (boton && bloque) {
      boton.addEventListener("click", () => {
        bloque.hidden = !bloque.hidden;
        boton.textContent = bloque.hidden ? "+ Seleccionar y añadir" : "− Cerrar";
        if (!bloque.hidden && filtro) filtro.focus();
      });
    }
    if (botonNuevo && campoNuevo) {
      botonNuevo.addEventListener("click", () => {
        campoNuevo.hidden = !campoNuevo.hidden;
        if (!campoNuevo.hidden) {
          const primero = campoNuevo.querySelector("input");
          if (primero) primero.focus();
        }
      });
    }
    if (filtro && elegibles) {
      filtro.addEventListener("input", () => {
        const texto = filtro.value.trim().toLowerCase();
        $$(".campo-elegible", elegibles).forEach((f) => {
          if (f.dataset.puesto === "1") return;  // ese ya está en la ficha
          f.hidden = !!texto && !f.dataset.nombre.includes(texto);
        });
      });
    }

    function anadirElegidos() {
      if (!elegibles) return;
      const elegidos = $$(".chk-campo:checked", elegibles);
      if (!elegidos.length) return;
      let primero = null;
      elegidos.forEach((caja) => {
        let opciones = [];
        try {
          opciones = JSON.parse(caja.dataset.opciones || "[]");
        } catch (_) {
          opciones = [];
        }
        const nuevo = filaDeCampo(caja.value, caja.dataset.nombre, caja.dataset.tipo, opciones);
        lista.append(nuevo.fila);
        if (!primero) primero = nuevo.control;
        caja.checked = false;
        const casilla = caja.closest(".campo-elegible");
        casilla.hidden = true;
        casilla.dataset.puesto = "1";
      });
      revisarVacio();
      if (bloque && boton) {
        bloque.hidden = true;
        boton.textContent = "+ Seleccionar y añadir";
      }
      if (primero) primero.focus();
    }

    const confirmar = $("#boton-confirmar-campos");
    if (confirmar) confirmar.addEventListener("click", anadirElegidos);
    if (elegibles) {
      elegibles.addEventListener("dblclick", (e) => {
        const casilla = e.target.closest(".campo-elegible");
        if (!casilla) return;
        casilla.querySelector(".chk-campo").checked = true;
        anadirElegidos();
      });
    }

    lista.addEventListener("click", (e) => {
      const aspa = e.target.closest(".quitar-campo");
      if (!aspa) return;
      const fila = aspa.closest(".campo-fila");
      const pk = fila.dataset.campo;
      if (fila.classList.contains("nueva")) {
        fila.remove();  // todavía no estaba guardado: fuera y ya está
      } else {
        // Uno que sí estaba guardado: se manda vacío y sin la marca de
        // conservarlo, que es justo lo que le dice al servidor que lo borre.
        const control = fila.querySelector(`[name="campo_${pk}"]`);
        if (control) control.value = "";
        const marca = fila.querySelector('[name="campo_mantener"]');
        if (marca) marca.remove();
        fila.hidden = true;
      }
      if (elegibles) {
        const devuelto = $(`.chk-campo[value="${pk}"]`, elegibles);
        if (devuelto) {
          const casilla = devuelto.closest(".campo-elegible");
          casilla.hidden = false;
          delete casilla.dataset.puesto;
        }
      }
      revisarVacio();
    });
  }

  /* --- Recién llegados ------------------------------------------------------- */
  /* Al catalogar uno desde esa colección su fila sobra: se va de la lista y el
     contador de la barra lateral baja, sin recargar la página entera. */
  function sacarDeRecien(id) {
    const insignia = $(".fila-lateral.recien .insignia");
    if (insignia) {
      insignia.textContent = Math.max(0, parseInt(insignia.textContent, 10) - 1);
    }
    if (!/[?&]vista=por_revisar/.test(location.search)) return;
    const fila = $(`#documentos [data-id="${id}"]`);
    if (fila) fila.remove();
  }

  /* --- Guardar la ficha sin recargar ---------------------------------------- */
  function engancharFicha() {
    const ficha = $("#ficha-documento");
    if (!ficha) return;
    engancharCampos();

    // «Ya está»: lo saca de Recién llegados sin tener que ponerle nada.
    const botonCatalogado = $("#boton-catalogado");
    const marca = $("#marca-revisado");
    if (botonCatalogado && marca) {
      botonCatalogado.addEventListener("click", () => {
        marca.value = "1";
        ficha.requestSubmit();
      });
    }

    ficha.addEventListener("submit", (e) => {
      e.preventDefault();
      const id = ficha.action.match(/doc\/(\d+)/)[1];
      const estabaPendiente = !!$("#aviso-pendiente");
      fetch(ficha.action, {
        method: "POST",
        body: new FormData(ficha),
        headers: { "X-Parcial": "1", "X-CSRFToken": csrf() },
      })
        .then((r) => r.text())
        .then((html) => {
          detalle.innerHTML = html;
          engancharFicha();
          const aviso = $("#aviso-guardado");
          if (aviso) {
            aviso.hidden = false;
            setTimeout(() => (aviso.hidden = true), 1800);
          }
          const fila = $(`#documentos [data-id="${id}"]`);
          const titulo = $("#titulo-detalle");
          if (fila && titulo) {
            const celda = fila.querySelector(".titulo") || fila.querySelector("h4");
            if (celda) celda.textContent = titulo.textContent;
          }
          if (estabaPendiente && !$("#aviso-pendiente")) sacarDeRecien(id);
        });
    });
  }
  engancharFicha();

  /* --- Selección múltiple ---------------------------------------------------- */
  const acciones = $("#acciones-seleccion");
  const contador = $("#contador-seleccion");
  function seleccionados() {
    return $$(".chk:checked").map((c) => c.value);
  }
  function refrescarSeleccion() {
    const n = seleccionados().length;
    if (acciones) acciones.hidden = n === 0;
    if (contador) contador.textContent = `${n} seleccionado${n === 1 ? "" : "s"}`;
  }
  document.addEventListener("change", (e) => {
    if (e.target.id === "chk-todos") {
      $$(".chk").forEach((c) => (c.checked = e.target.checked));
    }
    if (e.target.classList.contains("chk") || e.target.id === "chk-todos") refrescarSeleccion();
  });
  const formAcciones = $("#form-acciones");
  if (formAcciones) {
    formAcciones.addEventListener("submit", () => {
      formAcciones.querySelectorAll('input[name="ids"]').forEach((i) => i.remove());
      seleccionados().forEach((id) => {
        const oculto = document.createElement("input");
        oculto.type = "hidden";
        oculto.name = "ids";
        oculto.value = id;
        formAcciones.appendChild(oculto);
      });
    });
  }
  refrescarSeleccion();

  /* --- Subir documentos ------------------------------------------------------ */
  /* Ventana propia con zona de soltar y botón de buscar. Va dentro de un
     <dialog>: mientras está cerrada no existe para el ratón, que es lo que
     antes convertía la zona de soltar en una lámina pegada tapando la
     aplicación. Se pueden acumular varios ficheros antes de mandarlos. */
  const dialogoSubida = $("#dialogo-subida");
  const formSubida = $("#formulario-subida");
  const entradaFicheros = $("#ficheros");
  const botonSubir = $("#boton-subir");
  const zona = $("#zona-soltar");
  const listaSubida = $("#lista-subida");
  const estadoSubida = $("#estado-subida");
  const botonEnviar = $("#boton-enviar-subida");
  const botonVaciar = $("#boton-vaciar-subida");
  let pendientes = [];

  function pesoLegible(bytes) {
    const unidades = ["B", "KB", "MB", "GB"];
    let n = bytes, i = 0;
    while (n >= 1024 && i < unidades.length - 1) {
      n /= 1024;
      i += 1;
    }
    return `${i ? n.toFixed(1) : n} ${unidades[i]}`;
  }

  function pintarPendientes() {
    if (!listaSubida) return;
    listaSubida.textContent = "";
    pendientes.forEach((fichero, i) => {
      const linea = document.createElement("li");
      const nombre = document.createElement("span");
      nombre.className = "nombre";
      nombre.textContent = fichero.name;
      const peso = document.createElement("em");
      peso.textContent = pesoLegible(fichero.size);
      const quitar = document.createElement("button");
      quitar.type = "button";
      quitar.className = "quitar-fichero";
      quitar.title = "Quitar de la lista";
      quitar.textContent = "×";
      quitar.addEventListener("click", () => {
        pendientes.splice(i, 1);
        pintarPendientes();
      });
      linea.append(nombre, peso, quitar);
      listaSubida.append(linea);
    });
    const n = pendientes.length;
    if (botonEnviar) {
      botonEnviar.disabled = n === 0;
      botonEnviar.textContent = n ? `Subir ${n} documento${n === 1 ? "" : "s"}` : "Subir";
    }
    if (botonVaciar) botonVaciar.hidden = n === 0;
    if (estadoSubida) estadoSubida.textContent = "";
  }

  function anadirFicheros(ficheros) {
    Array.from(ficheros || []).forEach((f) => {
      const repetido = pendientes.some((p) => p.name === f.name && p.size === f.size);
      if (!repetido) pendientes.push(f);
    });
    pintarPendientes();
  }

  if (botonSubir && dialogoSubida) {
    botonSubir.addEventListener("click", () => {
      pintarPendientes();
      dialogoSubida.showModal();
    });
  }
  const botonCerrarSubida = $("#boton-cerrar-subida");
  if (botonCerrarSubida) botonCerrarSubida.addEventListener("click", () => dialogoSubida.close());
  if (botonVaciar) {
    botonVaciar.addEventListener("click", () => {
      pendientes = [];
      pintarPendientes();
    });
  }
  const botonBuscar = $("#boton-buscar-fichero");
  if (botonBuscar && entradaFicheros) {
    botonBuscar.addEventListener("click", () => entradaFicheros.click());
    entradaFicheros.addEventListener("change", () => {
      anadirFicheros(entradaFicheros.files);
      entradaFicheros.value = "";  // para poder volver a elegir el mismo
    });
  }

  if (dialogoSubida) {
    // Si se suelta fuera de la zona, que el navegador no se vaya a abrir el PDF.
    ["dragover", "drop"].forEach((ev) =>
      dialogoSubida.addEventListener(ev, (e) => e.preventDefault())
    );
  }
  if (zona) {
    ["dragenter", "dragover"].forEach((ev) =>
      zona.addEventListener(ev, (e) => {
        e.preventDefault();
        zona.classList.add("encima");
      })
    );
    ["dragleave", "drop"].forEach((ev) =>
      zona.addEventListener(ev, () => zona.classList.remove("encima"))
    );
    zona.addEventListener("drop", (e) => {
      e.preventDefault();
      anadirFicheros(e.dataTransfer.files);
    });
  }

  if (botonEnviar) botonEnviar.addEventListener("click", enviar);

  function enviar() {
    if (!pendientes.length || !formSubida) return;
    const datos = new FormData();
    pendientes.forEach((f) => datos.append("ficheros", f));
    datos.append("csrfmiddlewaretoken", csrf());

    botonEnviar.disabled = true;
    if (estadoSubida) estadoSubida.textContent = "Subiendo…";

    const fallo = (motivo) => {
      botonEnviar.disabled = false;
      if (estadoSubida) estadoSubida.textContent = motivo;
    };

    const peticion = new XMLHttpRequest();
    peticion.open("POST", formSubida.action);
    peticion.setRequestHeader("X-Parcial", "1");
    peticion.upload.addEventListener("progress", (e) => {
      if (!e.lengthComputable || !estadoSubida) return;
      estadoSubida.textContent = `Subiendo… ${Math.round((e.loaded / e.total) * 100)}%`;
    });
    peticion.addEventListener("load", () => {
      let respuesta = {};
      try {
        respuesta = JSON.parse(peticion.responseText);
      } catch (_) {
        respuesta = {};
      }
      if (peticion.status >= 400 || !respuesta.ok) {
        fallo("No se pudieron subir. Míralo en Estado.");
        return;
      }
      const creados = respuesta.creados || [];
      if (respuesta.fallidos) {
        alert(`${respuesta.fallidos} no se pudieron archivar. El detalle está en Estado.`);
      }
      // Y directos a «Recién llegados», con el primero ya abierto para catalogar.
      const destino = new URL("/", location.origin);
      destino.searchParams.set("vista", "por_revisar");
      if (creados.length) destino.searchParams.set("doc", creados[0]);
      location.href = destino.toString();
    });
    peticion.addEventListener("error", () => fallo("Se cortó la conexión."));
    peticion.send(datos);
  }
})();
