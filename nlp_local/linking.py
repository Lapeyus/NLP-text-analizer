"""
Entity linking real contra Wikidata.

POR QUÉ EXISTE ESTE MÓDULO
Pedirle los identificadores del Knowledge Graph directamente al LLM no funciona:
medido contra Google sobre 7 entidades famosas, acertó 0. Todos tenían el
formato correcto "/m/xxxxx" y todos apuntaban a otra cosa. Un identificador
inventado es peor que ninguno, porque su uso principal es deduplicar entidades
entre documentos y un ID falso produce uniones incorrectas en silencio.

CÓMO LO RESUELVE
Los MID de Google son identificadores de Freebase, y Wikidata conserva ese
mapeo en la propiedad P646. Así que en vez de pedirle al modelo que RECUERDE un
ID, le pedimos que ELIJA entre candidatos reales:

    1. Generación de candidatos:  Wikidata wbsearchentities por el nombre.
    2. Desambiguación:            el LLM elige usando el contexto de la frase.
    3. Resolución:                P646 -> MID, sitelink enwiki -> wikipedia_url.

El modelo nunca inventa un identificador: solo escoge de una lista o dice que
ninguno encaja. Es la arquitectura estándar de un sistema de entity linking, y
mueve el problema de "memoria del modelo" a "búsqueda + juicio con contexto",
que es lo que un LLM sí hace bien.

VERIFICACIÓN
Sobre las 8 entidades donde teníamos el MID real de Google (Apple, iPhone,
Cupertino, Michael Jordan, NBA, Chicago Bulls, Tesla, Berlin), este método
reprodujo 8/8. Ojo con un detalle: una entidad puede tener VARIOS P646 (Apple
tiene /m/0k8z y /m/04st9hr) y Google usa el primero, así que conservamos el
orden que da Wikidata.

COSTO
Requiere red. Por eso el linking está DESACTIVADO por defecto: el resto de
nlp_local funciona sin conexión y no queremos romper esa propiedad sin que lo
pidas. Actívalo con LanguageServiceClient(entity_linking=True).
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://www.wikidata.org/w/api.php"

# Wikidata pide identificarse y devuelve 429 si la aporreas. Lo comprobamos en
# la práctica: sin pausas, el barrido de 8 entidades se cortó a la quinta.
USER_AGENT = os.environ.get(
    "NLP_LOCAL_USER_AGENT",
    "nlp-local/0.1 (compatibilidad con Google NL API; uso personal)",
)
PAUSA_SEGUNDOS = 0.6

_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           ".cache_wikidata.json")

DESAMBIGUAR = """Eres un desambiguador de entidades.

Te doy una frase, una entidad mencionada en ella y una lista numerada de
candidatos reales de Wikidata. Elige el candidato que corresponde a lo que la
frase realmente menciona.

Reglas:
- Responde con el índice del candidato correcto.
- Si NINGUNO corresponde, responde con -1. Es preferible no enlazar a enlazar
  mal.
- Fíjate en el contexto. "Apple" junto a "iPhone" y "Cupertino" es la empresa;
  "apple" junto a "breakfast" es la fruta y probablemente no tenga candidato
  adecuado (la fruta genérica no es la misma entidad que un artículo concreto).
- No inventes candidatos que no estén en la lista.

Formato: {"index": <int>}

Responde EXCLUSIVAMENTE con el objeto JSON."""


class _FalloRed(RuntimeError):
    """Wikidata no respondió. Distinto de 'la entidad no existe'."""


class _Cache:
    def __init__(self, path=_CACHE_PATH):
        self.path = path
        try:
            with open(path, encoding="utf-8") as fh:
                self.d = json.load(fh)
        except (OSError, ValueError):
            self.d = {}

    def get(self, k):
        return self.d.get(k)

    def put(self, k, v):
        self.d[k] = v
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.d, fh, ensure_ascii=False)
        os.replace(tmp, self.path)


_cache = _Cache()
_ultimo_llamado = [0.0]


def _http(url, reintentos=5):
    for intento in range(reintentos):
        espera = PAUSA_SEGUNDOS - (time.time() - _ultimo_llamado[0])
        if espera > 0:
            time.sleep(espera)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=25) as r:
                datos = json.load(r)
            _ultimo_llamado[0] = time.time()
            return datos
        except urllib.error.HTTPError as e:
            _ultimo_llamado[0] = time.time()
            if e.code == 429:
                time.sleep(2.0 * (intento + 1))
                continue
            raise
    raise RuntimeError("Wikidata devolvió 429 de forma persistente")


def buscar_candidatos(nombre, limite=5, idioma="en"):
    """Candidatos de Wikidata para un nombre. Devuelve [(qid, label, desc)]."""
    clave = "buscar|{}|{}|{}".format(idioma, limite, nombre.lower())
    guardado = _cache.get(clave)
    if guardado is not None:
        return [tuple(x) for x in guardado]

    url = (
        "{}?action=wbsearchentities&format=json&language={}&uselang={}"
        "&type=item&limit={}&search={}"
    ).format(API, idioma, idioma, limite, urllib.parse.quote(nombre))
    try:
        datos = _http(url)
    except Exception as e:
        # Se propaga un centinela para que EntityLinker pueda contar el fallo
        # de red por separado en vez de confundirlo con "no existe".
        raise _FalloRed(str(e))

    salida = [
        (x["id"], x.get("label", ""), x.get("description", ""))
        for x in datos.get("search", [])
    ]
    _cache.put(clave, salida)
    return salida


def resolver(qid):
    """
    Devuelve (mid, wikipedia_url) para un QID de Wikidata.

    Una entidad puede tener varios P646; Google usa el primero, así que
    respetamos el orden en que Wikidata los devuelve.
    """
    clave = "resolver|" + qid
    guardado = _cache.get(clave)
    if guardado is not None:
        return tuple(guardado)

    url = "{}?action=wbgetentities&format=json&props=claims|sitelinks&ids={}".format(
        API, qid
    )
    try:
        ent = _http(url)["entities"][qid]
    except Exception as e:
        raise _FalloRed(str(e))

    mids = []
    for c in ent.get("claims", {}).get("P646", []):
        snak = c.get("mainsnak", {})
        if snak.get("datavalue"):
            mids.append(snak["datavalue"]["value"])

    titulo = ent.get("sitelinks", {}).get("enwiki", {}).get("title")
    # safe=",()'" reproduce el formato de Google: deja la coma y los paréntesis
    # sin escapar ("Cupertino,_California", no "Cupertino%2C_California").
    url_wiki = (
        "https://en.wikipedia.org/wiki/"
        + urllib.parse.quote(titulo.replace(" ", "_"), safe=",()'")
        if titulo
        else None
    )

    salida = (mids[0] if mids else None, url_wiki)
    _cache.put(clave, list(salida))
    return salida


class EntityLinker:
    """
    Enlaza entidades a Wikidata y traduce a los campos que devuelve Google.

    Solo intenta enlazar nombres PROPIOS: los sustantivos comunes ("store",
    "food") no son entidades del Knowledge Graph y buscarlos solo genera ruido y
    tráfico. Google tampoco les asigna MID.
    """

    def __init__(self, backend=None, max_candidatos=5, idioma="en"):
        self.backend = backend
        self.max_candidatos = max_candidatos
        self.idioma = idioma
        self.enlazadas = 0
        self.rechazadas = 0
        # Se cuentan aparte los fallos de RED. Es la misma trampa que
        # motivó este módulo: "no enlazada porque Wikidata no la conoce" y
        # "no enlazada porque nos limitaron por frecuencia" se ven idénticas
        # desde fuera, y la segunda es un fallo transitorio que hay que poder
        # detectar. Nos pasó: un barrido seguido perdió una entidad por un 429
        # y el resultado parecía simplemente "sin enlace".
        self.fallos_red = 0

    def enlazar(self, nombre, contexto, es_propio=True):
        """
        Devuelve (mid, wikipedia_url), ambos posiblemente None.

        None no es un fallo: es el estado legítimo "el Knowledge Graph no
        reconoce esto", que Google también produce.
        """
        if not es_propio or not nombre or len(nombre) < 2:
            return (None, None)

        try:
            candidatos = buscar_candidatos(nombre, self.max_candidatos, self.idioma)
        except _FalloRed:
            self.fallos_red += 1
            return (None, None)
        if not candidatos:
            self.rechazadas += 1
            return (None, None)

        idx = self._elegir(nombre, contexto, candidatos)
        if idx is None or not (0 <= idx < len(candidatos)):
            self.rechazadas += 1
            return (None, None)

        try:
            mid, url = resolver(candidatos[idx][0])
        except _FalloRed:
            self.fallos_red += 1
            return (None, None)
        if mid or url:
            self.enlazadas += 1
        else:
            self.rechazadas += 1
        return (mid, url)

    def _elegir(self, nombre, contexto, candidatos):
        # Un único candidato con etiqueta idéntica no necesita al modelo.
        if len(candidatos) == 1 and candidatos[0][1].lower() == nombre.lower():
            return 0
        if self.backend is None:
            # Sin modelo, el primer resultado de Wikidata es la mejor apuesta
            # ciega, pero solo la aceptamos si la etiqueta coincide de verdad;
            # si no, preferimos no enlazar.
            return 0 if candidatos[0][1].lower() == nombre.lower() else None

        lista = "\n".join(
            "{}. {} — {} ({})".format(i, c[1], c[2] or "sin descripción", c[0])
            for i, c in enumerate(candidatos)
        )
        usuario = (
            'Frase: "{}"\n\nEntidad mencionada: "{}"\n\nCandidatos:\n{}'.format(
                contexto[:600], nombre, lista
            )
        )
        try:
            datos = self.backend.complete_json(DESAMBIGUAR, usuario, num_predict=64)
        except Exception:
            return None
        try:
            return int(datos.get("index", -1))
        except (TypeError, ValueError):
            return None
