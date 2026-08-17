"""
Backends de inferencia. Un backend solo sabe una cosa: recibir un prompt y
devolver un dict de Python. Todo lo que sea "cómo se ve la respuesta de Google"
vive fuera de aquí.

Esa separación es la que hace que el modelo sea reemplazable: para cambiar de
modelo (o de motor de inferencia) basta con pasar otro backend al cliente, sin
tocar ni los prompts ni la conversión a objetos de Google.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("NLP_LOCAL_MODEL", "qwen3.8:27b-mlx")

# Los modelos MLX de Ollama ignoran `format` con esquema JSON: no aplican
# decodificación restringida por gramática. Por eso no dependemos de esa función
# y extraemos el JSON del texto a mano. Ver _extract_json().
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class BackendError(RuntimeError):
    """El backend no logró producir un dict utilizable."""


def _extract_json(text):
    """
    Saca el primer objeto/array JSON del texto del modelo.

    Necesario porque no podemos forzar la gramática de salida. Aunque el prompt
    pida "solo JSON", un modelo puede envolverlo en ```json, precederlo de una
    frase, o cerrar mal las llaves. Esta función es la red de seguridad.
    """
    if not text:
        raise BackendError("respuesta vacía del modelo")

    s = text.strip()

    # 1) ```json ... ```
    m = _FENCE.search(s)
    if m:
        s = m.group(1).strip()

    # 2) Intento directo.
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 3) Recorte al primer bloque balanceado { } o [ ].
    for abre, cierra in (("{", "}"), ("[", "]")):
        ini = s.find(abre)
        if ini == -1:
            continue
        prof = 0
        en_cadena = False
        escapa = False
        for i in range(ini, len(s)):
            c = s[i]
            if escapa:
                escapa = False
                continue
            if c == "\\":
                escapa = True
                continue
            if c == '"':
                en_cadena = not en_cadena
                continue
            if en_cadena:
                continue
            if c == abre:
                prof += 1
            elif c == cierra:
                prof -= 1
                if prof == 0:
                    try:
                        return json.loads(s[ini:i + 1])
                    except json.JSONDecodeError:
                        break

    raise BackendError(
        "no se encontró JSON válido en la respuesta: {!r}".format(text[:200])
    )


class OllamaBackend:
    """
    Backend contra un servidor Ollama local.

    El modelo es un parámetro, no una constante: cámbialo en el constructor o
    con la variable de entorno NLP_LOCAL_MODEL.

        OllamaBackend()                          # usa NLP_LOCAL_MODEL o el default
        OllamaBackend(model="qwen3.5:9b-mlx")    # más rápido, menos preciso
        OllamaBackend(model="gemma4:31b-mlx")    # otra familia

    Sobre los parámetros no obvios:

    think=False
        Estos modelos traen razonamiento en cadena activado por defecto. Con él,
        una sola llamada de sentimiento tardaba 70-108 s y devolvía prosa en
        markdown en vez de JSON. Apagarlo la baja a ~1 s. Para tareas de
        anotación estructurada el razonamiento largo no aporta y sí estorba.

    temperature=0.0
        Queremos que la misma entrada dé la misma salida. Un analizador que
        cambia de respuesta entre corridas no es comparable contra Google ni
        cacheable.
    """

    def __init__(
        self,
        model=None,
        host=None,
        temperature=0.0,
        num_predict=1024,
        think=False,
        timeout=300,
        retries=2,
    ):
        self.model = model or DEFAULT_MODEL
        self.host = (host or DEFAULT_HOST).rstrip("/")
        self.temperature = temperature
        self.num_predict = num_predict
        self.think = think
        self.timeout = timeout
        self.retries = retries
        # Métricas de la sesión, útiles para el arnés de paridad.
        self.calls = 0
        self.total_seconds = 0.0

    def __repr__(self):
        return "OllamaBackend(model={!r}, host={!r})".format(self.model, self.host)

    def complete_json(self, system_prompt, user_prompt, num_predict=None):
        """
        Manda el prompt y devuelve el dict/list ya parseado.

        Reintenta ante JSON inválido endureciendo la instrucción, porque el
        fallo típico de un modelo local no es equivocarse de contenido sino
        adornar la salida con prosa.
        """
        ultimo_error = None
        sistema = system_prompt

        for intento in range(self.retries + 1):
            try:
                crudo = self._chat(sistema, user_prompt, num_predict)
                return _extract_json(crudo)
            except BackendError as e:
                ultimo_error = e
                # En el reintento somos más explícitos.
                sistema = (
                    system_prompt
                    + "\n\nIMPORTANTE: tu respuesta anterior no fue JSON válido. "
                    "Responde EXCLUSIVAMENTE con el objeto JSON. Sin explicación, "
                    "sin markdown, sin ```. El primer carácter debe ser { o [."
                )
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                ultimo_error = BackendError(
                    "no se pudo contactar a Ollama en {}: {}".format(self.host, e)
                )
                break

        raise ultimo_error

    def _chat(self, system_prompt, user_prompt, num_predict=None):
        cuerpo = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "think": self.think,
            "options": {
                "temperature": self.temperature,
                "num_predict": num_predict or self.num_predict,
            },
        }
        req = urllib.request.Request(
            self.host + "/api/chat",
            data=json.dumps(cuerpo).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            datos = json.load(resp)
        self.calls += 1
        self.total_seconds += time.time() - t0
        return datos.get("message", {}).get("content", "")


class CachingBackend:
    """
    Envuelve otro backend y memoriza respuestas en disco.

    Existe por el arnés de paridad: cada iteración de ajuste vuelve a correr el
    mismo corpus, y sin caché cada vuelta cuesta minutos de inferencia. La clave
    incluye el modelo y el prompt, así que cambiar cualquiera de los dos
    invalida la entrada correspondiente y solo esa.
    """

    def __init__(self, inner, path=None):
        self.inner = inner
        self.path = path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".cache_llm.json"
        )
        self.hits = 0
        self.misses = 0
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                self._cache = json.load(fh)
        except (OSError, ValueError):
            self._cache = {}

    @property
    def model(self):
        return self.inner.model

    def __repr__(self):
        return "CachingBackend({!r})".format(self.inner)

    def complete_json(self, system_prompt, user_prompt, num_predict=None):
        clave = json.dumps(
            [self.inner.model, system_prompt, user_prompt, num_predict],
            ensure_ascii=False,
        )
        if clave in self._cache:
            self.hits += 1
            return self._cache[clave]
        self.misses += 1
        valor = self.inner.complete_json(system_prompt, user_prompt, num_predict)
        self._cache[clave] = valor
        self._flush()
        return valor

    def _flush(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._cache, fh, ensure_ascii=False)
        os.replace(tmp, self.path)
