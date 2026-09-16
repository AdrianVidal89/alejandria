/* Alejandria — JavaScript de andar por casa: sin dependencias, sin compilar.
   Solo tres cosas: selección + teclado, refresco del panel de detalle por AJAX
   y arrastrar ficheros a la ventana. Todo lo demás lo pinta Django. */
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

  /* --- Guardar la ficha sin recargar ---------------------------------------- */
  function engancharFicha() {
    const ficha = $("#ficha-documento");
    if (!ficha) return;
    ficha.addEventListener("submit", (e) => {
      e.preventDefault();
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
          const fila = $(`#documentos [data-id="${ficha.action.match(/doc\/(\d+)/)[1]}"]`);
          const titulo = $("#titulo-detalle");
          if (fila && titulo) {
            const celda = fila.querySelector(".titulo") || fila.querySelector("h4");
            if (celda) celda.textContent = titulo.textContent;
          }
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

  /* --- Añadir documentos: botón y arrastrar sobre la ventana ---------------- */
  const formSubida = $("#formulario-subida");
  const entradaFicheros = $("#ficheros");
  const zona = $("#zona-soltar");
  const botonSubir = $("#boton-subir");
  if (botonSubir && entradaFicheros) {
    botonSubir.addEventListener("click", () => entradaFicheros.click());
    entradaFicheros.addEventListener("change", () => enviar(entradaFicheros.files));
  }
  // La zona de soltar se controla con un temporizador, no contando entradas y
  // salidas: mientras se arrastra algo encima, el navegador dispara "dragover"
  // sin parar, así que basta con esconderla en cuanto dejan de llegar. Si el
  // arrastre termina de cualquier forma rara —soltar fuera de la ventana, Escape,
  // el navegador cancelando— desaparece sola. Contando eventos se quedaba pegada
  // y tapaba la aplicación entera.
  let temporizadorZona = null;

  function mostrarZona() {
    if (!zona) return;
    zona.hidden = false;
    clearTimeout(temporizadorZona);
    temporizadorZona = setTimeout(ocultarZona, 250);
  }

  function ocultarZona() {
    clearTimeout(temporizadorZona);
    if (zona) zona.hidden = true;
  }

  window.addEventListener("dragover", (e) => {
    e.preventDefault();
    if (e.dataTransfer && Array.from(e.dataTransfer.types).includes("Files")) mostrarZona();
  });
  window.addEventListener("dragend", ocultarZona);
  window.addEventListener("mouseup", ocultarZona);
  window.addEventListener("blur", ocultarZona);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") ocultarZona();
  });
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    ocultarZona();
    if (e.dataTransfer && e.dataTransfer.files.length) enviar(e.dataTransfer.files);
  });

  function enviar(ficheros) {
    if (!ficheros || !ficheros.length || !formSubida) return;
    const datos = new FormData();
    Array.from(ficheros).forEach((f) => datos.append("ficheros", f));
    datos.append("csrfmiddlewaretoken", csrf());
    if (botonSubir) botonSubir.textContent = `Subiendo ${ficheros.length}…`;
    fetch(formSubida.action, { method: "POST", body: datos, headers: { "X-Parcial": "1" } })
      .then((r) => r.json())
      .then(() => location.reload())
      .catch(() => {
        if (botonSubir) botonSubir.textContent = "＋ Añadir";
        alert("No se pudieron subir los documentos.");
      });
  }
})();
