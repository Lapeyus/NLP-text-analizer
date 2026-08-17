"""
nlp_local — implementación compatible con la Google Cloud Natural Language API,
respaldada por un LLM local a través de Ollama.

USO COMO REEMPLAZO DIRECTO
La forma pensada de usarlo es cambiar una sola línea de import:

    # antes
    from google.cloud import language_v1

    # después
    from nlp_local import language_v1

A partir de ahí, `language_v1.LanguageServiceClient()`, los enums
(`language_v1.Entity.Type`), los tipos de documento y las respuestas se usan
igual. Los enums y los tipos de datos NO son imitaciones: se reexportan tal cual
desde google-cloud-language, así que son los mismos objetos.

ELEGIR MODELO

    from nlp_local import LanguageServiceClient
    c = LanguageServiceClient()                        # default o NLP_LOCAL_MODEL
    c = LanguageServiceClient(model="qwen3.5:9b-mlx")  # rápido
    c = LanguageServiceClient(model="gemma4:31b-mlx")  # otra familia

QUÉ NO HACE
- No lee de Cloud Storage (`gcs_content_uri`): descarga el contenido tú.
- No implementa la v2 de la API.
- `annotate_text` hace una llamada al modelo por cada feature activada; no es
  más rápido que llamar a los métodos por separado, solo más cómodo.

PARIDAD
La carpeta parity/ contiene el arnés que compara esta implementación contra la
API oficial de Google y reporta métricas por análisis. Ver parity/README.md.
"""

from .backend import BackendError, CachingBackend, OllamaBackend
from .client import LanguageServiceClient

__all__ = [
    "LanguageServiceClient",
    "OllamaBackend",
    "CachingBackend",
    "BackendError",
    "language_v1",
]

__version__ = "0.1.0"


class _CompatV1:
    """
    Fachada que imita al módulo `google.cloud.language_v1`.

    Reexporta TODO lo del módulo real (enums, tipos de documento, clases de
    respuesta) y solo sustituye LanguageServiceClient por el nuestro. Así
    `from nlp_local import language_v1` es intercambiable con
    `from google.cloud import language_v1` sin que nada más cambie de tipo.
    """

    def __init__(self):
        from google.cloud import language_v1 as _real

        self._real = _real
        self.LanguageServiceClient = LanguageServiceClient

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)

    def __dir__(self):
        return sorted(set(dir(self._real)) | {"LanguageServiceClient"})

    def __repr__(self):
        return "<nlp_local.language_v1 (compatible con google.cloud.language_v1)>"


language_v1 = _CompatV1()
