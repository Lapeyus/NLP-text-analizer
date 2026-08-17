#!/usr/bin/env python3
"""
CLI unificado: el mismo análisis contra Google o contra un LLM local.

    ./analyze.py sentiment "The food was amazing but the service was terrible."
    ./analyze.py --backend local entities "Apple opened a store in Mexico City."
    ./analyze.py --compare sentiment "Great price. Awful build quality."

LA IDEA CENTRAL
Fíjate en `_ejecutar()`: no hay ni un `if backend == "google"` dentro de la
lógica de análisis. El backend se elige UNA vez, al construir el cliente, y a
partir de ahí las llamadas y el formateo son idénticos. Eso es lo que significa
que las dos implementaciones sean intercambiables, y este archivo existe en
buena parte para demostrarlo de forma ejecutable.

Para el uso normal hay dos atajos ya listos:

    ./analyze-google sentiment "..."
    ./analyze-local  sentiment "..."

Si lo que buscas es entender CADA análisis con ejemplos y cuándo usarlo, ese
material está en main.py, que es la referencia comentada. Este archivo es la
herramienta.
"""

import argparse
import sys

from google.cloud import language_v1

ANALISIS = [
    "sentiment", "sentences", "entities", "entity-sentiment",
    "classify", "moderate", "syntax", "link", "annotate",
]

BACKENDS = ["google", "local"]


# --------------------------------------------------------------------------
# Selección de backend: el único punto donde las dos opciones se diferencian
# --------------------------------------------------------------------------
def crear_cliente(backend, model=None, entity_linking=False):
    """
    Devuelve un cliente. Los dos exponen la MISMA interfaz y devuelven los
    mismos tipos, así que a partir de aquí nada más necesita saber cuál es.
    """
    if backend == "google":
        return language_v1.LanguageServiceClient()

    if backend == "local":
        try:
            import nlp_local
        except ImportError:
            sys.exit(
                "No se pudo importar nlp_local. Corre desde la raíz del repo."
            )
        return nlp_local.LanguageServiceClient(
            model=model, entity_linking=entity_linking
        )

    raise ValueError("backend desconocido: {}".format(backend))


def _doc(texto, idioma=None, html=False):
    d = {
        "content": texto,
        "type_": (language_v1.Document.Type.HTML if html
                  else language_v1.Document.Type.PLAIN_TEXT),
    }
    if idioma:
        d["language"] = idioma
    return d


# --------------------------------------------------------------------------
# Formateadores: uno por análisis, todos devuelven una lista de líneas
# --------------------------------------------------------------------------
def _f_sentiment(r):
    s = r.document_sentiment
    lineas = ["score={:+.2f}   magnitude={:.2f}".format(s.score, s.magnitude)]
    if abs(s.score) < 0.15 and s.magnitude >= 1.0:
        lineas.append("  (score bajo + magnitude alta = MIXTO, no neutral)")
    return lineas


def _f_sentences(r):
    s = r.document_sentiment
    out = ["global: score={:+.2f} magnitude={:.2f}".format(s.score, s.magnitude)]
    for o in r.sentences:
        out.append("  [{:+.2f}] {}".format(o.sentiment.score, o.text.content))
    return out


def _f_entities(r, con_sentimiento=False):
    out = []
    for e in r.entities:
        linea = "{:<22} {:<14} salience={:.3f}".format(
            e.name[:22], language_v1.Entity.Type(e.type_).name, e.salience
        )
        if con_sentimiento:
            linea += "  sentiment={:+.2f}".format(e.sentiment.score)
        mid = e.metadata.get("mid")
        if mid:
            linea += "  mid=" + mid
        out.append(linea)
    return out or ["(sin entidades)"]


def _f_classify(r):
    return [
        "{:<58} {:.3f}".format(c.name[:58], c.confidence) for c in r.categories
    ] or ["(sin categorías)"]


def _f_moderate(r, umbral=0.5):
    activas = [c for c in r.moderation_categories if c.confidence >= umbral]
    if not activas:
        return ["(nada por encima de {:.2f} — {} categorías evaluadas)".format(
            umbral, len(r.moderation_categories))]
    return ["{:<26} {:.3f}".format(c.name, c.confidence)
            for c in sorted(activas, key=lambda x: -x.confidence)]


def _f_syntax(r):
    out = []
    for t in r.tokens:
        out.append("{:<14} {:<7} lemma={:<14} head={:<3} {}".format(
            t.text.content[:14],
            language_v1.PartOfSpeech.Tag(t.part_of_speech.tag).name,
            t.lemma[:14],
            t.dependency_edge.head_token_index,
            language_v1.DependencyEdge.Label(t.dependency_edge.label).name,
        ))
    return out


def _f_link(r):
    enlazadas = [e for e in r.entities if e.metadata.get("mid")]
    sueltas = [e for e in r.entities if not e.metadata.get("mid")]
    out = ["enlazadas al Knowledge Graph:"]
    out += ["  {:<20} {:<14} {:<12} {}".format(
        e.name[:20], language_v1.Entity.Type(e.type_).name,
        e.metadata.get("mid", ""), e.metadata.get("wikipedia_url", ""))
        for e in enlazadas] or ["  (ninguna)"]
    out.append("sin enlace:")
    out += ["  {:<20} {}".format(e.name[:20],
            language_v1.Entity.Type(e.type_).name)
            for e in sueltas] or ["  (ninguna)"]
    return out


def _f_annotate(r):
    out = ["sentimiento: score={:+.2f} magnitude={:.2f}".format(
        r.document_sentiment.score, r.document_sentiment.magnitude)]
    if r.entities:
        out.append("entidades:")
        out += ["  " + x for x in _f_entities(r, con_sentimiento=True)]
    if r.categories:
        out.append("categorías:")
        out += ["  " + x for x in _f_classify(r)]
    activas = [c for c in r.moderation_categories if c.confidence >= 0.5]
    if activas:
        out.append("moderación:")
        out += ["  {:<26} {:.3f}".format(c.name, c.confidence) for c in activas]
    return out


# --------------------------------------------------------------------------
# Despacho
# --------------------------------------------------------------------------
def _ejecutar(cliente, analisis, texto, idioma=None, html=False):
    """
    Corre un análisis y devuelve las líneas ya formateadas.

    Aquí NO se pregunta qué backend es. Es el punto del ejercicio: una vez
    construido el cliente, el resto del programa no distingue.
    """
    doc = _doc(texto, idioma, html)
    u = language_v1.EncodingType.UTF8

    if analisis == "sentiment":
        return _f_sentiment(
            cliente.analyze_sentiment(request={"document": doc, "encoding_type": u}))
    if analisis == "sentences":
        return _f_sentences(
            cliente.analyze_sentiment(request={"document": doc, "encoding_type": u}))
    if analisis == "entities":
        return _f_entities(
            cliente.analyze_entities(request={"document": doc, "encoding_type": u}))
    if analisis == "entity-sentiment":
        return _f_entities(
            cliente.analyze_entity_sentiment(
                request={"document": doc, "encoding_type": u}),
            con_sentimiento=True)
    if analisis == "classify":
        return _f_classify(cliente.classify_text(request={"document": doc}))
    if analisis == "moderate":
        return _f_moderate(cliente.moderate_text(request={"document": doc}))
    if analisis == "syntax":
        return _f_syntax(
            cliente.analyze_syntax(request={"document": doc, "encoding_type": u}))
    if analisis == "link":
        return _f_link(
            cliente.analyze_entities(request={"document": doc, "encoding_type": u}))
    if analisis == "annotate":
        return _f_annotate(cliente.annotate_text(request={
            "document": doc,
            "features": {
                "extract_entities": True,
                "extract_document_sentiment": True,
                "extract_entity_sentiment": True,
                "classify_text": True,
                "moderate_text": True,
                "extract_syntax": False,
            },
            "encoding_type": u,
        }))
    raise ValueError(analisis)


def _seguro(fn):
    """Convierte una excepción en líneas legibles en vez de un traceback."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - queremos reportar cualquier fallo
        return ["ERROR {}: {}".format(type(e).__name__, str(e).split("\n")[0][:110])]


def _comparar(analisis, texto, model, idioma, html, entity_linking):
    """
    Corre los dos backends sobre el mismo texto y los muestra en paralelo.

    Es la forma rápida de decidir si el modelo local te sirve para TU texto,
    sin montar el arnés completo de parity/.
    """
    g = crear_cliente("google")
    l = crear_cliente("local", model=model, entity_linking=entity_linking)

    izq = _seguro(lambda: _ejecutar(g, analisis, texto, idioma, html))
    der = _seguro(lambda: _ejecutar(l, analisis, texto, idioma, html))

    ancho = max([len(x) for x in izq] + [40]) + 2
    ancho = min(ancho, 78)
    print("{:<{w}}| {}".format("GOOGLE", "LOCAL ({})".format(l.model), w=ancho))
    print("-" * ancho + "+" + "-" * ancho)
    for i in range(max(len(izq), len(der))):
        a = izq[i] if i < len(izq) else ""
        b = der[i] if i < len(der) else ""
        print("{:<{w}}| {}".format(a[:ancho - 1], b, w=ancho))


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="analyze.py",
        description="Analiza texto con la API de Google o con un LLM local.",
        epilog="Atajos: ./analyze-google y ./analyze-local",
    )
    p.add_argument("analisis", choices=ANALISIS, help="análisis a ejecutar")
    p.add_argument("texto", nargs="?", help="texto a analizar (o usa --file / stdin)")
    p.add_argument("--backend", choices=BACKENDS, default="google",
                   help="motor a usar (por defecto: google)")
    p.add_argument("--compare", action="store_true",
                   help="corre AMBOS backends y muestra el resultado en paralelo")
    p.add_argument("--model", default=None,
                   help="modelo de Ollama (solo backend local)")
    p.add_argument("--entity-linking", action="store_true",
                   help="resuelve entidades contra Wikidata (local; requiere red)")
    p.add_argument("--file", help="lee el texto de un archivo")
    p.add_argument("--language", default=None,
                   help="código de idioma (ej. es). Si se omite, se detecta")
    p.add_argument("--html", action="store_true",
                   help="trata la entrada como HTML en vez de texto plano")
    args = p.parse_args(argv)

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            texto = fh.read()
    elif args.texto:
        texto = args.texto
    elif not sys.stdin.isatty():
        texto = sys.stdin.read()
    else:
        p.error("falta el texto: pásalo como argumento, con --file o por stdin")

    texto = texto.strip()
    if not texto:
        p.error("el texto está vacío")

    if args.compare:
        _comparar(args.analisis, texto, args.model, args.language, args.html,
                  args.entity_linking)
        return 0

    cliente = crear_cliente(args.backend, model=args.model,
                            entity_linking=args.entity_linking)
    etiqueta = args.backend
    if args.backend == "local":
        etiqueta += " ({})".format(cliente.model)

    print("== {} · {} ==".format(args.analisis, etiqueta))
    for linea in _ejecutar(cliente, args.analisis, texto, args.language, args.html):
        print(linea)
    return 0


if __name__ == "__main__":
    sys.exit(main())
