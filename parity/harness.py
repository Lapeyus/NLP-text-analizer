"""
Arnés de paridad: corre el maestro (Google) y el alumno (nlp_local) sobre el
mismo corpus y reporta cuánto se parecen.

    python -m parity.harness                       # todo
    python -m parity.harness sentiment entities    # solo esos análisis
    python -m parity.harness --model qwen3.5:9b-mlx
    python -m parity.harness --no-cache            # ignora la caché local

Las respuestas de Google se cachean en disco: el maestro no cambia entre
iteraciones y cada llamada cuesta cuota. Las del modelo local se cachean en
nlp_local/.cache_llm.json por la misma razón, pero esa caché se invalida sola
cuando cambias el prompt o el modelo, que es justo lo que iteramos.
"""

import argparse
import json
import os
import sys
import time

from google.cloud import language_v1 as G

import nlp_local
from parity import corpus, metrics

CACHE_GOOGLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            ".cache_google.json")

ANALISIS = ["sentiment", "entities", "entity_sentiment", "classify",
            "moderate", "syntax"]


def _doc(texto, idioma="en"):
    return {"content": texto, "type_": G.Document.Type.PLAIN_TEXT,
            "language": idioma}


def _llamar(cliente, analisis, texto, idioma):
    doc = _doc(texto, idioma)
    u = G.EncodingType.UTF8
    if analisis == "sentiment":
        return cliente.analyze_sentiment(request={"document": doc, "encoding_type": u})
    if analisis == "entities":
        return cliente.analyze_entities(request={"document": doc, "encoding_type": u})
    if analisis == "entity_sentiment":
        return cliente.analyze_entity_sentiment(
            request={"document": doc, "encoding_type": u})
    if analisis == "classify":
        return cliente.classify_text(request={"document": doc})
    if analisis == "moderate":
        return cliente.moderate_text(request={"document": doc})
    if analisis == "syntax":
        return cliente.analyze_syntax(request={"document": doc, "encoding_type": u})
    raise ValueError(analisis)


_TIPO_RESPUESTA = {
    "sentiment": G.AnalyzeSentimentResponse,
    "entities": G.AnalyzeEntitiesResponse,
    "entity_sentiment": G.AnalyzeEntitySentimentResponse,
    "classify": G.ClassifyTextResponse,
    "moderate": G.ModerateTextResponse,
    "syntax": G.AnalyzeSyntaxResponse,
}


def _comparar_errores(err_g, err_l):
    """
    Puntúa el caso en el que al menos una de las dos implementaciones falló.

    Coincidir en el error es paridad legítima: un consumidor que hace
    try/except InvalidArgument se comporta igual con las dos librerías. Fallar
    solo en una es el peor caso posible, porque rompe al cambiar de motor.
    """
    if err_g is not None and err_l is not None:
        mismo_tipo = type(err_g).__name__ == type(err_l).__name__
        return {
            "google_error": type(err_g).__name__,
            "local_error": type(err_l).__name__,
            "mismo_tipo": mismo_tipo,
            "score": 100.0 if mismo_tipo else 40.0,
        }
    if err_g is not None:
        return {
            "google_error": type(err_g).__name__ + ": " + str(err_g)[:70],
            "local_error": "(ninguno: devolvió respuesta)",
            "score": 0.0,
        }
    return {
        "google_error": "(ninguno: devolvió respuesta)",
        "local_error": type(err_l).__name__ + ": " + str(err_l)[:70],
        "score": 0.0,
    }


class CacheGoogle:
    """Cachea las respuestas del maestro serializándolas como JSON del proto."""

    def __init__(self, activa=True):
        self.activa = activa
        self.hits = 0
        self.misses = 0
        try:
            with open(CACHE_GOOGLE, encoding="utf-8") as fh:
                self._d = json.load(fh)
        except (OSError, ValueError):
            self._d = {}

    def obtener(self, cliente, analisis, clave, texto, idioma):
        k = "{}|{}".format(analisis, clave)
        tipo = _TIPO_RESPUESTA[analisis]
        if self.activa and k in self._d:
            self.hits += 1
            return tipo.from_json(json.dumps(self._d[k]))
        self.misses += 1
        r = _llamar(cliente, analisis, texto, idioma)
        self._d[k] = json.loads(tipo.to_json(r))
        self._guardar()
        return r

    def _guardar(self):
        tmp = CACHE_GOOGLE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._d, fh, ensure_ascii=False)
        os.replace(tmp, CACHE_GOOGLE)


def evaluar(analisis_pedidos, model=None, usar_cache=True, verbose=True):
    g_cli = G.LanguageServiceClient()
    l_cli = nlp_local.LanguageServiceClient(model=model, cache=usar_cache)
    cache_g = CacheGoogle(activa=usar_cache)

    resumen = {}
    t0 = time.time()

    for analisis in analisis_pedidos:
        filas = []
        for clave, texto in corpus.textos_para(analisis):
            idioma = "es" if clave.startswith("es-") else "en"

            # Los errores forman parte del contrato: si Google rechaza una
            # entrada, una implementación compatible debe rechazarla igual. Por
            # eso comparamos también las excepciones en vez de saltarnos el caso.
            err_g = err_l = None
            rg = rl = None
            try:
                rg = cache_g.obtener(g_cli, analisis, clave, texto, idioma)
            except Exception as e:
                err_g = e
            try:
                rl = _llamar(l_cli, analisis, texto, idioma)
            except Exception as e:
                err_l = e

            if err_g is not None or err_l is not None:
                filas.append((clave, _comparar_errores(err_g, err_l)))
                continue

            if analisis == "sentiment":
                m = metrics.sentiment(rg, rl)
            elif analisis == "entities":
                m = metrics.entities(rg, rl, con_sentimiento=False)
            elif analisis == "entity_sentiment":
                m = metrics.entities(rg, rl, con_sentimiento=True)
            elif analisis == "classify":
                m = metrics.classify(rg, rl)
            elif analisis == "moderate":
                m = metrics.moderate(rg, rl)
            else:
                m = metrics.syntax(rg, rl)
            filas.append((clave, m))

        puntajes = [m["score"] for _, m in filas]
        media = round(sum(puntajes) / len(puntajes), 1) if puntajes else 0.0
        resumen[analisis] = {"media": media, "filas": filas}

        if verbose:
            print("\n" + "=" * 72)
            print("{}   paridad media: {:.1f}/100".format(analisis.upper(), media))
            print("=" * 72)
            for clave, m in filas:
                print("  {:<18} {:>5.1f}  {}".format(
                    clave, m["score"],
                    " ".join("{}={}".format(k, v) for k, v in m.items()
                             if k != "score")[:150]))

    if verbose:
        print("\n" + "#" * 72)
        print("RESUMEN   modelo={}".format(l_cli.model))
        print("#" * 72)
        for a, d in resumen.items():
            barra = "#" * int(d["media"] / 4)
            print("  {:<18} {:>5.1f}  {}".format(a, d["media"], barra))
        glob = [d["media"] for d in resumen.values()]
        print("  {:<18} {:>5.1f}".format("GLOBAL",
                                         round(sum(glob) / len(glob), 1) if glob else 0))
        print("\n  google: {} llamadas nuevas, {} desde caché".format(
            cache_g.misses, cache_g.hits))
        b = l_cli.backend
        if hasattr(b, "hits"):
            print("  local : {} inferencias nuevas, {} desde caché".format(
                b.misses, b.hits))
            b = b.inner
        if hasattr(b, "total_seconds"):
            print("  tiempo de inferencia: {:.1f}s en {} llamadas".format(
                b.total_seconds, b.calls))
        print("  total: {:.1f}s".format(time.time() - t0))

    return resumen


def main(argv=None):
    p = argparse.ArgumentParser(description="Arnés de paridad Google vs local")
    p.add_argument("analisis", nargs="*", default=None,
                   help="subconjunto a evaluar ({})".format(", ".join(ANALISIS)))
    p.add_argument("--model", default=None, help="modelo de Ollama a usar")
    p.add_argument("--no-cache", action="store_true")
    args = p.parse_args(argv)

    pedidos = args.analisis or ANALISIS
    malos = [a for a in pedidos if a not in ANALISIS]
    if malos:
        p.error("análisis desconocido: {}".format(", ".join(malos)))

    evaluar(pedidos, model=args.model, usar_cache=not args.no_cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
