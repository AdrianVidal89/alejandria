"""Vista previa de Word (.docx), Markdown y texto plano, sin dependencias.

Un .docx es un zip con XML dentro: se lee `word/document.xml` con la librería
estándar y se traduce a HTML sencillo (títulos, párrafos, negritas, listas,
tablas, enlaces e imágenes incrustadas). El Markdown se traduce con un
intérprete pequeño que cubre lo que se escribe a diario. Todo el texto se
escapa antes de montar el HTML: lo que venga dentro del fichero nunca llega al
navegador como marcado.

Nada de esto es exacto al milímetro —para eso está «Abrir»—, pero basta para
reconocer el documento de un vistazo sin salir de la biblioteca.
"""
import base64
import html
import re
import zipfile
from xml.etree import ElementTree

# Techos para que un documento raro no se coma la memoria del NAS.
MAX_BYTES_TEXTO = 2 * 1024 * 1024       # Markdown / texto que se interpreta
MAX_BYTES_XML = 24 * 1024 * 1024        # document.xml descomprimido
MAX_BYTES_IMAGENES = 6 * 1024 * 1024    # imágenes incrustadas, en total

EXTENSIONES_MARKDOWN = ("md", "markdown")
EXTENSIONES_WORD = ("docx",)
EXTENSIONES_TEXTO = ("txt", "csv", "log", "json", "xml", "yml", "yaml")


class SinVista(Exception):
    """El fichero no se puede enseñar (dañado, enorme o de otro formato)."""


def soportado(extension):
    return extension in EXTENSIONES_MARKDOWN + EXTENSIONES_WORD + EXTENSIONES_TEXTO


def generar(ruta, extension):
    """HTML (ya seguro) del cuerpo del documento."""
    if extension in EXTENSIONES_WORD:
        return docx_a_html(ruta)
    texto = _leer_texto(ruta)
    if extension in EXTENSIONES_MARKDOWN:
        return markdown_a_html(texto)
    return f'<pre class="texto-plano">{html.escape(texto)}</pre>'


def _leer_texto(ruta):
    with open(ruta, "rb") as f:
        crudo = f.read(MAX_BYTES_TEXTO + 1)
    cortado = len(crudo) > MAX_BYTES_TEXTO
    crudo = crudo[:MAX_BYTES_TEXTO]
    for codificacion in ("utf-8-sig", "cp1252"):
        try:
            texto = crudo.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    else:
        texto = crudo.decode("utf-8", errors="replace")
    if cortado:
        texto += "\n\n…(vista previa recortada: el fichero es muy grande)"
    return texto


# --- Enlaces seguros ----------------------------------------------------------
def _url_segura(url, imagen=False):
    url = (url or "").strip()
    if re.match(r"^(https?:|mailto:|#)", url, re.I):
        return url
    if imagen and re.match(r"^data:image/(png|jpe?g|gif|webp);base64,", url, re.I):
        return url
    return None


# =============================================================================
# Markdown
# =============================================================================
_EN_LINEA = re.compile(
    r"(?P<codigo>`+)(?P<codigo_txt>.+?)(?P=codigo)"
    r"|!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
    r"|\[(?P<txt>[^\]]+)\]\((?P<href>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
    r"|<(?P<auto>https?://[^>\s]+)>"
    r"|(?P<fuerte>\*\*|__)(?P<fuerte_txt>.+?)(?P=fuerte)"
    r"|(?P<tachado>~~)(?P<tachado_txt>.+?)~~"
    r"|(?<![\w*])(?P<enf>[*_])(?P<enf_txt>[^\s*_](?:.*?[^\s])?)(?P=enf)(?![\w*])"
)


def _en_linea(texto):
    """Formato dentro de una línea. Devuelve HTML con todo lo demás escapado."""
    salida, pos = [], 0
    for m in _EN_LINEA.finditer(texto):
        salida.append(html.escape(texto[pos:m.start()]))
        pos = m.end()
        if m.group("codigo"):
            salida.append(f"<code>{html.escape(m.group('codigo_txt').strip())}</code>")
        elif m.group("src") is not None:
            src = _url_segura(m.group("src"), imagen=True)
            alt = html.escape(m.group("alt"))
            salida.append(
                f'<img src="{html.escape(src)}" alt="{alt}">' if src else f"[{alt}]"
            )
        elif m.group("href") is not None:
            href = _url_segura(m.group("href"))
            interior = _en_linea(m.group("txt"))
            salida.append(
                f'<a href="{html.escape(href)}" target="_blank" rel="noopener">{interior}</a>'
                if href else interior
            )
        elif m.group("auto"):
            url = html.escape(m.group("auto"))
            salida.append(f'<a href="{url}" target="_blank" rel="noopener">{url}</a>')
        elif m.group("fuerte"):
            salida.append(f"<strong>{_en_linea(m.group('fuerte_txt'))}</strong>")
        elif m.group("tachado"):
            salida.append(f"<del>{_en_linea(m.group('tachado_txt'))}</del>")
        elif m.group("enf"):
            salida.append(f"<em>{_en_linea(m.group('enf_txt'))}</em>")
    salida.append(html.escape(texto[pos:]))
    # Dos espacios (o barra invertida) al final de línea = salto forzado.
    return re.sub(r"(?: {2,}|\\)\n", "<br>\n", "".join(salida))


_TITULO = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_VALLA = re.compile(r"^\s*(```|~~~)\s*([\w+-]*)")
_REGLA = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_ELEMENTO = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_CITA = re.compile(r"^\s*>\s?(.*)$")
_SEPARADOR_TABLA = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_SUBRAYADO = re.compile(r"^\s*(=+|-+)\s*$")


def _celdas(linea):
    linea = linea.strip()
    if linea.startswith("|"):
        linea = linea[1:]
    if linea.endswith("|") and not linea.endswith("\\|"):
        linea = linea[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", linea)]


def markdown_a_html(texto):
    lineas = texto.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ").split("\n")
    return "\n".join(_bloques(lineas))


def _bloques(lineas):
    salida, i, n = [], 0, len(lineas)
    while i < n:
        linea = lineas[i]

        if not linea.strip():
            i += 1
            continue

        # Bloque de código con valla.
        valla = _VALLA.match(linea)
        if valla:
            cierre, lenguaje = valla.group(1), valla.group(2)
            codigo = []
            i += 1
            while i < n and not lineas[i].strip().startswith(cierre):
                codigo.append(lineas[i])
                i += 1
            i += 1
            clase = f' class="lenguaje-{html.escape(lenguaje)}"' if lenguaje else ""
            salida.append(f"<pre><code{clase}>{html.escape(chr(10).join(codigo))}</code></pre>")
            continue

        titulo = _TITULO.match(linea)
        if titulo:
            nivel = len(titulo.group(1))
            salida.append(f"<h{nivel}>{_en_linea(titulo.group(2))}</h{nivel}>")
            i += 1
            continue

        if _REGLA.match(linea):
            salida.append("<hr>")
            i += 1
            continue

        # Código sangrado (cuatro espacios).
        if linea.startswith("    ") and not _ELEMENTO.match(linea):
            codigo = []
            while i < n and (lineas[i].startswith("    ") or not lineas[i].strip()):
                codigo.append(lineas[i][4:])
                i += 1
            salida.append(f"<pre><code>{html.escape(chr(10).join(codigo).rstrip())}</code></pre>")
            continue

        if _CITA.match(linea):
            dentro = []
            while i < n and lineas[i].strip() and _CITA.match(lineas[i]):
                dentro.append(_CITA.match(lineas[i]).group(1))
                i += 1
            salida.append(f"<blockquote>{chr(10).join(_bloques(dentro))}</blockquote>")
            continue

        # Tabla: cabecera + fila separadora.
        if "|" in linea and i + 1 < n and _SEPARADOR_TABLA.match(lineas[i + 1]):
            alineaciones = []
            for c in _celdas(lineas[i + 1]):
                if c.startswith(":") and c.endswith(":"):
                    alineaciones.append("center")
                elif c.endswith(":"):
                    alineaciones.append("right")
                else:
                    alineaciones.append("")

            def fila(celdas, etiqueta):
                partes = []
                for j, c in enumerate(celdas):
                    al = alineaciones[j] if j < len(alineaciones) else ""
                    estilo = f' style="text-align:{al}"' if al else ""
                    partes.append(f"<{etiqueta}{estilo}>{_en_linea(c)}</{etiqueta}>")
                return "<tr>" + "".join(partes) + "</tr>"

            cabecera = fila(_celdas(linea), "th")
            i += 2
            cuerpo = []
            while i < n and lineas[i].strip() and "|" in lineas[i]:
                cuerpo.append(fila(_celdas(lineas[i]), "td"))
                i += 1
            salida.append(
                f"<table><thead>{cabecera}</thead><tbody>{''.join(cuerpo)}</tbody></table>"
            )
            continue

        if _ELEMENTO.match(linea):
            html_lista, i = _lista(lineas, i)
            salida.append(html_lista)
            continue

        # Párrafo: hasta línea en blanco o hasta que empiece otro bloque.
        parrafo = [linea.lstrip() if linea.endswith("  ") else linea.strip()]
        i += 1
        while i < n and lineas[i].strip():
            siguiente = lineas[i]
            if _SUBRAYADO.match(siguiente) and len(parrafo) == 1:
                nivel = 1 if siguiente.strip().startswith("=") else 2
                salida.append(f"<h{nivel}>{_en_linea(parrafo[0])}</h{nivel}>")
                parrafo = []
                i += 1
                break
            if (_TITULO.match(siguiente) or _VALLA.match(siguiente) or _CITA.match(siguiente)
                    or _REGLA.match(siguiente) or _ELEMENTO.match(siguiente)):
                break
            parrafo.append(siguiente.strip() if not siguiente.endswith("  ") else siguiente.lstrip())
            i += 1
        if parrafo:
            salida.append(f"<p>{_en_linea(chr(10).join(parrafo))}</p>")
    return salida


def _lista(lineas, i):
    """Lista (con anidamiento por sangría). Devuelve (html, índice siguiente)."""
    primera = _ELEMENTO.match(lineas[i])
    sangria = len(primera.group(1))
    ordenada = primera.group(2)[0].isdigit()
    etiqueta = "ol" if ordenada else "ul"
    inicio = ""
    if ordenada and int(primera.group(2)[:-1]) != 1:
        inicio = f' start="{int(primera.group(2)[:-1])}"'

    elementos, n = [], len(lineas)
    while i < n:
        m = _ELEMENTO.match(lineas[i])
        if not m or len(m.group(1)) < sangria:
            if m is None and lineas[i].strip() and elementos and lineas[i].startswith(" " * (sangria + 2)):
                elementos[-1][0].append(lineas[i].strip())  # continuación del elemento
                i += 1
                continue
            break
        if len(m.group(1)) > sangria:
            sub, i = _lista(lineas, i)
            if elementos:
                elementos[-1][1].append(sub)
            continue
        if m.group(2)[0].isdigit() != ordenada:
            break
        elementos.append(([m.group(3)], []))
        i += 1
        # Una línea en blanco entre elementos no corta la lista.
        if i < n and not lineas[i].strip() and i + 1 < n:
            siguiente = _ELEMENTO.match(lineas[i + 1])
            if siguiente and len(siguiente.group(1)) >= sangria:
                i += 1

    partes = []
    for texto, sublistas in elementos:
        contenido = " ".join(texto)
        tarea = re.match(r"^\[([ xX])\]\s+(.*)$", contenido)
        if tarea:
            marcada = " checked" if tarea.group(1).lower() == "x" else ""
            cuerpo = f'<input type="checkbox" disabled{marcada}> {_en_linea(tarea.group(2))}'
            partes.append(f'<li class="tarea">{cuerpo}{"".join(sublistas)}</li>')
        else:
            partes.append(f"<li>{_en_linea(contenido)}{''.join(sublistas)}</li>")
    return f"<{etiqueta}{inicio}>{''.join(partes)}</{etiqueta}>", i


# =============================================================================
# Word (.docx)
# =============================================================================
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
V = "{urn:schemas-microsoft-com:vml}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_TIPOS_IMAGEN = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp",
}


def _val(nodo, ruta):
    """Atributo w:val de un hijo, o None."""
    if nodo is None:
        return None
    hijo = nodo.find(ruta)
    return None if hijo is None else hijo.get(f"{W}val")


def _activo(nodo, ruta):
    """Negrita/cursiva… en OOXML: presente y sin val="0"/"false"."""
    if nodo is None:
        return False
    hijo = nodo.find(ruta)
    return hijo is not None and hijo.get(f"{W}val", "true") not in ("0", "false", "none")


class _Docx:
    def __init__(self, zip_):
        self.zip = zip_
        self.relaciones = self._relaciones()
        self.estilos, self.listas_estilo = self._estilos()
        self.nivel_por_estilo = {}
        self.listas = self._numeraciones()
        self.bytes_imagenes = 0

    def _xml(self, nombre):
        try:
            info = self.zip.getinfo(nombre)
        except KeyError:
            return None
        if info.file_size > MAX_BYTES_XML:
            raise SinVista("El documento es demasiado grande para la vista previa.")
        return ElementTree.fromstring(self.zip.read(info))

    def _relaciones(self):
        raiz = self._xml("word/_rels/document.xml.rels")
        if raiz is None:
            return {}
        return {
            r.get("Id"): (r.get("Target", ""), r.get("TargetMode", ""))
            for r in raiz.iter(f"{REL}Relationship")
        }

    def _estilos(self):
        """Dos diccionarios por id de estilo: nivel de título (1-6) y lista.

        Muchas listas de Word no llevan la numeración en el párrafo sino en su
        estilo («Viñeta», «List Number»…), a veces heredada de otro estilo.
        """
        raiz = self._xml("word/styles.xml")
        niveles, listas = {}, {}
        if raiz is None:
            return niveles, listas
        bases, propias = {}, {}
        for estilo in raiz.iter(f"{W}style"):
            ident = estilo.get(f"{W}styleId", "")
            base = _val(estilo, f"{W}basedOn")
            if base:
                bases[ident] = base
            num = estilo.find(f"{W}pPr/{W}numPr")
            if num is not None:
                nivel = _val(num, f"{W}ilvl")
                # «List Bullet 2», «Viñeta 3»: listas de un solo nivel que
                # Word sangra por estilo. El número del nombre da el nivel.
                digito = re.search(r"\s([2-9])$", _val(estilo, f"{W}name") or "")
                if nivel is None and digito:
                    nivel = str(int(digito.group(1)) - 1)
                propias[ident] = (_val(num, f"{W}numId"), nivel)
            nombre = (_val(estilo, f"{W}name") or ident).lower().replace(" ", "")
            m = re.match(r"^(heading|título|titulo|encabezado)(\d)$", nombre)
            if m:
                niveles[ident] = min(6, int(m.group(2)))
            elif nombre in ("title", "título", "titulo"):
                niveles[ident] = 1
            elif nombre in ("subtitle", "subtítulo", "subtitulo"):
                niveles[ident] = 2
            else:
                nivel = _val(estilo, f"{W}pPr/{W}outlineLvl")
                if nivel is not None and nivel.isdigit() and int(nivel) < 6:
                    niveles[ident] = int(nivel) + 1
        for ident in set(bases) | set(propias):
            actual, vistos = ident, set()
            while actual and actual not in propias and actual not in vistos:
                vistos.add(actual)
                actual = bases.get(actual)
            if actual in propias:
                listas[ident] = propias[actual]
        return niveles, listas

    def _numeraciones(self):
        """numId → {nivel: ordenada?}. Solo interesa si es viñeta o número."""
        raiz = self._xml("word/numbering.xml")
        if raiz is None:
            return {}
        abstractos = {}
        for abstracto in raiz.iter(f"{W}abstractNum"):
            formatos = {}
            for lvl in abstracto.iter(f"{W}lvl"):
                formato = _val(lvl, f"{W}numFmt") or "bullet"
                formatos[lvl.get(f"{W}ilvl", "0")] = formato not in ("bullet", "none")
                # «Viñeta 2» y compañía dicen su nivel aquí, no en el estilo.
                estilo = _val(lvl, f"{W}pStyle")
                if estilo:
                    self.nivel_por_estilo[estilo] = lvl.get(f"{W}ilvl", "0")
            abstractos[abstracto.get(f"{W}abstractNumId")] = formatos
        return {
            num.get(f"{W}numId"): abstractos.get(_val(num, f"{W}abstractNumId"), {})
            for num in raiz.iter(f"{W}num")
        }

    # --- Texto ---------------------------------------------------------------
    def _imagen(self, rid):
        destino, modo = self.relaciones.get(rid, ("", ""))
        if not destino or modo == "External":
            return ""
        nombre = destino.lstrip("/") if destino.startswith("/") else f"word/{destino}"
        nombre = re.sub(r"[^/]+/\.\./", "", nombre)
        tipo = _TIPOS_IMAGEN.get(nombre.rsplit(".", 1)[-1].lower())
        if not tipo:
            return ""
        try:
            info = self.zip.getinfo(nombre)
        except KeyError:
            return ""
        if self.bytes_imagenes + info.file_size > MAX_BYTES_IMAGENES:
            return '<span class="sin-imagen">[imagen]</span>'
        self.bytes_imagenes += info.file_size
        datos = base64.b64encode(self.zip.read(info)).decode("ascii")
        return f'<img src="data:{tipo};base64,{datos}" alt="">'

    def _tramo(self, r):
        """Un <w:r>: texto con su formato."""
        partes = []
        for hijo in r:
            etiqueta = hijo.tag
            if etiqueta == f"{W}t":
                partes.append(html.escape(hijo.text or ""))
            elif etiqueta == f"{W}tab":
                partes.append("&emsp;")
            elif etiqueta in (f"{W}br", f"{W}cr"):
                partes.append("<br>")
            elif etiqueta == f"{W}noBreakHyphen":
                partes.append("‑")
            elif etiqueta in (f"{W}drawing", f"{W}pict", f"{W}object"):
                for blip in hijo.iter(f"{A}blip"):
                    partes.append(self._imagen(blip.get(f"{R}embed")))
                for imagen in hijo.iter(f"{V}imagedata"):
                    partes.append(self._imagen(imagen.get(f"{R}id")))
        texto = "".join(partes)
        if not texto:
            return ""
        rpr = r.find(f"{W}rPr")
        if _activo(rpr, f"{W}b"):
            texto = f"<strong>{texto}</strong>"
        if _activo(rpr, f"{W}i"):
            texto = f"<em>{texto}</em>"
        if _activo(rpr, f"{W}u"):
            texto = f"<u>{texto}</u>"
        if _activo(rpr, f"{W}strike") or _activo(rpr, f"{W}dstrike"):
            texto = f"<del>{texto}</del>"
        vertical = _val(rpr, f"{W}vertAlign")
        if vertical == "superscript":
            texto = f"<sup>{texto}</sup>"
        elif vertical == "subscript":
            texto = f"<sub>{texto}</sub>"
        return texto

    def _contenido(self, nodo):
        """Texto de un párrafo, recorriendo tramos, enlaces y controles."""
        partes = []
        for hijo in nodo:
            etiqueta = hijo.tag
            if etiqueta == f"{W}r":
                partes.append(self._tramo(hijo))
            elif etiqueta == f"{W}hyperlink":
                interior = self._contenido(hijo)
                destino, _ = self.relaciones.get(hijo.get(f"{R}id"), ("", ""))
                href = _url_segura(destino)
                partes.append(
                    f'<a href="{html.escape(href)}" target="_blank" rel="noopener">{interior}</a>'
                    if href and interior else interior
                )
            elif etiqueta in (f"{W}ins", f"{W}smartTag", f"{W}fldSimple", f"{W}customXml"):
                partes.append(self._contenido(hijo))
            elif etiqueta == f"{W}sdt":
                contenido = hijo.find(f"{W}sdtContent")
                if contenido is not None:
                    partes.append(self._contenido(contenido))
        return "".join(partes)

    def _parrafo(self, p):
        """Devuelve (html, lista) donde lista es (numId, nivel, ordenada) o None."""
        ppr = p.find(f"{W}pPr")
        interior = self._contenido(p)
        estilo = _val(ppr, f"{W}pStyle")
        nivel_titulo = self.estilos.get(estilo, 0) if estilo else 0

        num = ppr.find(f"{W}numPr") if ppr is not None else None
        de_estilo = self.listas_estilo.get(estilo, (None, None)) if estilo else (None, None)
        if (num is not None or de_estilo[0]) and nivel_titulo == 0:
            # Lo del párrafo manda; lo que falte se toma de su estilo.
            num_id = (_val(num, f"{W}numId") if num is not None else None) or de_estilo[0]
            nivel = ((_val(num, f"{W}ilvl") if num is not None else None) or de_estilo[1]
                     or self.nivel_por_estilo.get(estilo) or "0")
            if num_id and num_id != "0":
                ordenada = self.listas.get(num_id, {}).get(nivel, False)
                return interior, (num_id, int(nivel) if nivel.isdigit() else 0, ordenada)

        alineacion = _val(ppr, f"{W}jc")
        estilo_css = ""
        if alineacion in ("center", "right", "both"):
            estilo_css = f' style="text-align:{"justify" if alineacion == "both" else alineacion}"'
        if nivel_titulo:
            return f"<h{nivel_titulo}{estilo_css}>{interior}</h{nivel_titulo}>", None
        if not interior.strip():
            return '<p class="vacio"></p>', None
        return f"<p{estilo_css}>{interior}</p>", None

    def _tabla(self, tbl):
        filas = []
        for tr in tbl.findall(f"{W}tr"):
            celdas = []
            for tc in tr.findall(f"{W}tc"):
                tcpr = tc.find(f"{W}tcPr")
                if _val(tcpr, f"{W}vMerge") is None and tcpr is not None \
                        and tcpr.find(f"{W}vMerge") is not None:
                    continue  # continuación de una celda combinada en vertical
                span = _val(tcpr, f"{W}gridSpan")
                atributo = f' colspan="{int(span)}"' if span and span.isdigit() and int(span) > 1 else ""
                celdas.append(f"<td{atributo}>{self._cuerpo(tc)}</td>")
            filas.append(f"<tr>{''.join(celdas)}</tr>")
        return f"<table>{''.join(filas)}</table>"

    def _cuerpo(self, contenedor):
        """Párrafos y tablas de un contenedor, agrupando los elementos de lista."""
        salida = []
        pila = []  # [(nivel, etiqueta)] de listas abiertas

        def cerrar_hasta(nivel):
            while pila and pila[-1][0] > nivel:
                salida.append(f"</li></{pila.pop()[1]}>")

        for hijo in contenedor:
            if hijo.tag == f"{W}sdt":
                contenido = hijo.find(f"{W}sdtContent")
                if contenido is not None:
                    cerrar_hasta(-1)
                    salida.append(self._cuerpo(contenido))
                continue
            if hijo.tag == f"{W}p":
                interior, lista = self._parrafo(hijo)
                if lista is None:
                    cerrar_hasta(-1)
                    salida.append(interior)
                    continue
                _, nivel, ordenada = lista
                etiqueta = "ol" if ordenada else "ul"
                cerrar_hasta(nivel)
                if pila and pila[-1][0] == nivel:
                    if pila[-1][1] != etiqueta:
                        salida.append(f"</li></{pila.pop()[1]}><{etiqueta}><li>")
                        pila.append((nivel, etiqueta))
                    else:
                        salida.append("</li><li>")
                else:
                    salida.append(f"<{etiqueta}><li>")
                    pila.append((nivel, etiqueta))
                salida.append(interior)
            elif hijo.tag == f"{W}tbl":
                cerrar_hasta(-1)
                salida.append(self._tabla(hijo))
        cerrar_hasta(-1)
        return "".join(salida)

    def html(self):
        raiz = self._xml("word/document.xml")
        if raiz is None:
            raise SinVista("No parece un documento de Word.")
        cuerpo = raiz.find(f"{W}body")
        return self._cuerpo(cuerpo) if cuerpo is not None else ""


def docx_a_html(ruta):
    try:
        with zipfile.ZipFile(ruta) as z:
            return _Docx(z).html()
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, OSError) as e:
        raise SinVista("No se pudo leer el documento: parece dañado o no es un Word moderno.") from e
