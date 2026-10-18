"""El árbol de carpetas de la biblioteca, para el modo de navegación clásico.

Alejandria clasifica por etiquetas, corresponsal y tipo; esto es lo contrario:
recorrer las carpetas del disco tal cual, como el Finder o DevonThink. No hay
nada que guardar en la base de datos — el árbol se arma en memoria con las
carpetas que hay en disco y las rutas de las fichas, que es barato: son cuatro
cientos de cadenas de texto.
"""
from collections import defaultdict


def _ancestros(carpeta):
    """«a/b/c» → ['a', 'a/b', 'a/b/c']. La raíz es la cadena vacía."""
    partes = [p for p in carpeta.split("/") if p]
    salida, acumulado = [], ""
    for p in partes:
        acumulado = f"{acumulado}/{p}" if acumulado else p
        salida.append(acumulado)
    return salida


def _padre(carpeta):
    return carpeta.rsplit("/", 1)[0] if "/" in carpeta else ""


def armar(rutas, carpetas_disco=()):
    """Devuelve el mapa de carpetas con sus recuentos.

    `rutas` son las rutas relativas de los documentos visibles; `carpetas_disco`
    las carpetas que existen aunque estén vacías, para que una carpeta recién
    creada no se esconda hasta que caiga el primer papel dentro.

    De cada carpeta se guardan dos números distintos, porque los dos hacen
    falta: `directos` es lo que se va a listar al entrar (solo los papeles que
    cuelgan de ella) y `total` es lo que hay en toda la rama, que es lo que se
    enseña en el árbol.
    """
    directos = defaultdict(int)
    total = defaultdict(int)
    hijas = defaultdict(set)

    def registrar(carpeta):
        padre = _padre(carpeta)
        hijas[padre].add(carpeta)

    for carpeta in carpetas_disco:
        carpeta = carpeta.strip("/")
        if not carpeta:
            continue
        for rama in _ancestros(carpeta):
            registrar(rama)

    for ruta in rutas:
        carpeta = ruta.rsplit("/", 1)[0] if "/" in ruta else ""
        directos[carpeta] += 1
        total[""] += 1
        for rama in _ancestros(carpeta):
            total[rama] += 1
            registrar(rama)

    return {"directos": directos, "total": total, "hijas": hijas}


def ramas(mapa, actual=""):
    """El árbol plegado: la raíz entera y abierta solo por donde vas.

    Se devuelven las carpetas de primer nivel y, de las demás, únicamente las
    que cuelgan de la carpeta en la que estás o de alguno de sus ancestros. Así
    el árbol no se dispara a cientos de líneas y a la vez siempre se ve el
    camino por el que has entrado.
    """
    actual = (actual or "").strip("/")
    abiertas = {""} | set(_ancestros(actual))

    salida = []

    def recorrer(padre, nivel):
        for carpeta in sorted(mapa["hijas"].get(padre, ()), key=str.lower):
            tiene_hijas = bool(mapa["hijas"].get(carpeta))
            abierta = carpeta in abiertas
            salida.append({
                "ruta": carpeta,
                "nombre": carpeta.rsplit("/", 1)[-1],
                "nivel": nivel,
                "total": mapa["total"].get(carpeta, 0),
                "directos": mapa["directos"].get(carpeta, 0),
                "tiene_hijas": tiene_hijas,
                "abierta": abierta,
                "actual": carpeta == actual,
            })
            if abierta and tiene_hijas:
                recorrer(carpeta, nivel + 1)

    recorrer("", 0)
    return salida


def subcarpetas(mapa, carpeta=""):
    """Las carpetas que cuelgan directamente de esta, para listarlas arriba."""
    carpeta = (carpeta or "").strip("/")
    return [
        {
            "ruta": hija,
            "nombre": hija.rsplit("/", 1)[-1],
            "total": mapa["total"].get(hija, 0),
            "directos": mapa["directos"].get(hija, 0),
            "subcarpetas": len(mapa["hijas"].get(hija, ())),
        }
        for hija in sorted(mapa["hijas"].get(carpeta, ()), key=str.lower)
    ]


def migas(carpeta=""):
    """El camino de vuelta: [(nombre, ruta), …] sin incluir la raíz."""
    return [
        {"nombre": rama.rsplit("/", 1)[-1], "ruta": rama}
        for rama in _ancestros((carpeta or "").strip("/"))
    ]
