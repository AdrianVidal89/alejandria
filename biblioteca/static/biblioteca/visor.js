/* Alejandria — visor de PDF e imágenes con zoom.

   El zoom va en veces el «ancho ajustado» (1 = la hoja ocupa el ancho del
   visor). Al cambiarlo se estira la hoja con CSS en el acto y, un momento
   después, se vuelven a pintar las páginas visibles a la resolución nueva para
   que el texto salga nítido. Solo se pintan las páginas cerca de la vista: un
   PDF de cien páginas no se come la memoria del iPhone. */
(function () {
  "use strict";

  const MIN = 0.4;
  const MAX = 8;
  const PASO = 1.25;
  const MAX_PIXELES = 8e6;  // por lienzo; el iPhone se niega pasado unos 16 M

  let pdfjs = null;
  function cargarPdfjs(url, worker) {
    if (!pdfjs) {
      pdfjs = import(url).then((m) => {
        m.GlobalWorkerOptions.workerSrc = worker;
        return m;
      });
    }
    return pdfjs;
  }

  function crearVisor(raiz) {
    if (raiz.dataset.listo) return;
    raiz.dataset.listo = "1";

    const lienzo = raiz.querySelector(".visor-lienzo");
    const hojas = raiz.querySelector(".visor-hojas");
    const nivel = raiz.querySelector(".visor-nivel");
    const esPdf = raiz.dataset.tipo === "pdf";
    let zoom = 1;
    let base = 0;       // ancho en px de la hoja con zoom 1
    let anchoNatural = Infinity;  // las imágenes pequeñas no se agrandan al ajustar
    let pendiente = null;
    let repintar = () => {};  // el PDF pone aquí el suyo

    function medirBase() {
      const estilo = getComputedStyle(hojas);
      const relleno = parseFloat(estilo.paddingLeft) + parseFloat(estilo.paddingRight);
      const tope = raiz.classList.contains("grande") ? 1100 : Infinity;
      base = Math.max(120, Math.min(lienzo.clientWidth - relleno, tope, anchoNatural));
    }

    function aplicarAncho() {
      hojas.style.setProperty("--ancho-hoja", Math.round(base * zoom) + "px");
      if (nivel) nivel.textContent = Math.round(zoom * 100) + "%";
    }

    // Cambia el zoom dejando quieto el punto (fx, fy) del visor que se mira.
    function fijarZoom(nuevo, fx, fy) {
      nuevo = Math.min(MAX, Math.max(MIN, nuevo));
      if (Math.abs(nuevo - zoom) < 0.001) return;
      if (fx === undefined) {
        fx = lienzo.clientWidth / 2;
        fy = lienzo.clientHeight / 2;
      }
      const x = lienzo.scrollLeft + fx;
      const y = lienzo.scrollTop + fy;
      const r = nuevo / zoom;
      zoom = nuevo;
      aplicarAncho();
      lienzo.scrollLeft = x * r - fx;
      lienzo.scrollTop = y * r - fy;
      clearTimeout(pendiente);
      pendiente = setTimeout(() => repintar(), 180);
    }

    function relativo(clienteX, clienteY) {
      const caja = lienzo.getBoundingClientRect();
      return [clienteX - caja.left, clienteY - caja.top];
    }

    /* --- Botones ------------------------------------------------------------ */
    raiz.querySelector(".visor-mandos").addEventListener("click", (e) => {
      const boton = e.target.closest("[data-zoom]");
      if (!boton) return;
      const accion = boton.dataset.zoom;
      if (accion === "mas") fijarZoom(zoom * PASO);
      else if (accion === "menos") fijarZoom(zoom / PASO);
      else fijarZoom(1, 0, 0);
    });

    lienzo.addEventListener("keydown", (e) => {
      if (e.key === "+" || e.key === "=") { e.preventDefault(); fijarZoom(zoom * PASO); }
      else if (e.key === "-") { e.preventDefault(); fijarZoom(zoom / PASO); }
      else if (e.key === "0") { e.preventDefault(); fijarZoom(1, 0, 0); }
    });

    /* --- Ctrl + rueda (y el pellizco del trackpad, que llega igual) --------- */
    lienzo.addEventListener("wheel", (e) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const [fx, fy] = relativo(e.clientX, e.clientY);
      fijarZoom(zoom * Math.exp(-e.deltaY * 0.01), fx, fy);
    }, { passive: false });

    /* --- Pellizco y doble toque en pantallas táctiles ----------------------- */
    let gesto = null;
    let ultimoToque = 0;
    let toqueInicio = null;
    const distancia = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);

    lienzo.addEventListener("touchstart", (e) => {
      if (e.touches.length === 2) {
        const [a, b] = e.touches;
        gesto = { d: distancia(a, b), zoom };
        toqueInicio = null;
      } else if (e.touches.length === 1) {
        const t = e.touches[0];
        toqueInicio = { x: t.clientX, y: t.clientY, t: Date.now() };
      }
    }, { passive: true });

    lienzo.addEventListener("touchmove", (e) => {
      if (toqueInicio && e.touches.length === 1) {
        const t = e.touches[0];
        if (Math.hypot(t.clientX - toqueInicio.x, t.clientY - toqueInicio.y) > 10) toqueInicio = null;
      }
      if (!gesto || e.touches.length !== 2) return;
      e.preventDefault();
      const [a, b] = e.touches;
      const [fx, fy] = relativo((a.clientX + b.clientX) / 2, (a.clientY + b.clientY) / 2);
      fijarZoom(gesto.zoom * distancia(a, b) / gesto.d, fx, fy);
    }, { passive: false });

    lienzo.addEventListener("touchend", (e) => {
      if (e.touches.length < 2) gesto = null;
      if (!toqueInicio || e.touches.length || Date.now() - toqueInicio.t > 300) return;
      const ahora = Date.now();
      if (ahora - ultimoToque < 320) {
        e.preventDefault();
        alternar(toqueInicio.x, toqueInicio.y);
        ultimoToque = 0;
      } else {
        ultimoToque = ahora;
      }
      toqueInicio = null;
    });

    // Safari tiene sus propios eventos de gesto: que no amplíe la página entera.
    ["gesturestart", "gesturechange"].forEach((tipo) =>
      lienzo.addEventListener(tipo, (e) => e.preventDefault()));

    lienzo.addEventListener("dblclick", (e) => {
      if (Date.now() - ultimoToque < 800) return;  // ya lo llevó el toque
      e.preventDefault();
      alternar(e.clientX, e.clientY);
    });

    function alternar(clienteX, clienteY) {
      const [fx, fy] = relativo(clienteX, clienteY);
      if (zoom > 1.05) fijarZoom(1, 0, 0);
      else fijarZoom(2.5, fx, fy);
    }

    /* --- Tamaño del visor ---------------------------------------------------- */
    if ("ResizeObserver" in window) {
      let anterior = 0;
      new ResizeObserver(() => {
        if (lienzo.clientWidth === anterior) return;
        anterior = lienzo.clientWidth;
        medirBase();
        aplicarAncho();
        clearTimeout(pendiente);
        pendiente = setTimeout(() => repintar(), 180);
      }).observe(lienzo);
    }

    /* --- Imagen -------------------------------------------------------------- */
    if (!esPdf) {
      const img = hojas.querySelector("img");
      const listo = () => {
        if (img.naturalWidth) {
          anchoNatural = img.naturalWidth;
          img.parentElement.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`;
        }
        medirBase();
        aplicarAncho();
      };
      if (img.complete) listo(); else img.addEventListener("load", listo);
      medirBase();
      aplicarAncho();
      return;
    }

    /* --- PDF ----------------------------------------------------------------- */
    medirBase();
    aplicarAncho();
    const paginas = [];   // { hoja, pagina, tarea, ancho }
    const cerca = new Set();

    function pintar(p) {
      if (!p.pagina) return;
      const caja = p.hoja.clientWidth;
      if (!caja) return;
      const ppp = window.devicePixelRatio || 1;
      const vista1 = p.pagina.getViewport({ scale: 1 });
      let escala = (caja * ppp) / vista1.width;
      const pixeles = vista1.width * vista1.height * escala * escala;
      if (pixeles > MAX_PIXELES) escala *= Math.sqrt(MAX_PIXELES / pixeles);
      if (p.ancho && Math.abs(p.ancho - escala) / escala < 0.08) return;  // ya está bien
      if (p.tarea) p.tarea.cancel();
      const vista = p.pagina.getViewport({ scale: escala });
      const lienzoPagina = document.createElement("canvas");
      lienzoPagina.width = Math.floor(vista.width);
      lienzoPagina.height = Math.floor(vista.height);
      const tarea = p.pagina.render({ canvasContext: lienzoPagina.getContext("2d"), viewport: vista });
      p.tarea = tarea;
      tarea.promise.then(() => {
        p.tarea = null;
        p.ancho = escala;
        const viejo = p.hoja.querySelector("canvas");
        if (viejo) { viejo.replaceWith(lienzoPagina); viejo.width = 0; }
        else p.hoja.append(lienzoPagina);
      }).catch(() => {});
    }

    function soltar(p) {
      if (p.tarea) { p.tarea.cancel(); p.tarea = null; }
      const viejo = p.hoja.querySelector("canvas");
      if (viejo) { viejo.width = 0; viejo.remove(); }
      p.ancho = 0;
    }

    repintar = () => cerca.forEach((i) => pintar(paginas[i]));

    const vigia = new IntersectionObserver((entradas) => {
      entradas.forEach((en) => {
        const i = Number(en.target.dataset.pagina);
        if (en.isIntersecting) { cerca.add(i); pintar(paginas[i]); }
        else { cerca.delete(i); soltar(paginas[i]); }
      });
    }, { root: lienzo, rootMargin: "150% 50%" });

    function fallo() {
      hojas.innerHTML = "";
      const aviso = document.createElement("p");
      aviso.className = "visor-aviso";
      aviso.innerHTML = 'No se pudo pintar el PDF aquí. <a href="' + raiz.dataset.src +
        '" target="_blank" rel="noopener">Ábrelo tal cual</a>.';
      hojas.append(aviso);
    }

    cargarPdfjs(raiz.dataset.pdfjs, raiz.dataset.worker)
      .then((m) => m.getDocument({ url: raiz.dataset.src }).promise)
      .then(async (pdf) => {
        const primera = await pdf.getPage(1);
        const v = primera.getViewport({ scale: 1 });
        hojas.innerHTML = "";
        for (let i = 0; i < pdf.numPages; i++) {
          const hoja = document.createElement("div");
          hoja.className = "visor-hoja";
          hoja.dataset.pagina = i;
          hoja.style.aspectRatio = `${v.width} / ${v.height}`;
          hojas.append(hoja);
          paginas.push({ hoja, pagina: i === 0 ? primera : null, tarea: null, ancho: 0 });
        }
        paginas.forEach((p) => vigia.observe(p.hoja));
        // El resto de páginas, de una en una: sus tamaños pueden ser otros.
        for (let i = 1; i < pdf.numPages; i++) {
          const pagina = await pdf.getPage(i + 1);
          const vi = pagina.getViewport({ scale: 1 });
          const p = paginas[i];
          p.pagina = pagina;
          p.hoja.style.aspectRatio = `${vi.width} / ${vi.height}`;
          if (cerca.has(i)) pintar(p);
        }
      })
      .catch(fallo);
  }

  function enganchar(raiz) {
    (raiz || document).querySelectorAll(".visor").forEach(crearVisor);
  }

  /* --- Botón Volver de la página del visor ----------------------------------- */
  /* En la aplicación instalada no hay botón de atrás del navegador: si se
     vino desde Alejandria se vuelve por el historial (la lista se queda como
     estaba); si no, a la biblioteca con el documento abierto. */
  document.addEventListener("click", (e) => {
    const volver = e.target.closest("#volver-visor");
    if (!volver || e.ctrlKey || e.metaKey || e.shiftKey) return;
    let mismoSitio = false;
    try { mismoSitio = new URL(document.referrer).origin === location.origin; } catch (_) { /* sin referrer */ }
    if (mismoSitio && history.length > 1) {
      e.preventDefault();
      history.back();
    }
  });

  window.Visor = { enganchar };
  enganchar(document);
})();
