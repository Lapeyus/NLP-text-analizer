"""
Configuración común de la suite.

Todas las pruebas son offline: ninguna llama a la API de Google Natural
Language, a Ollama ni a Wikidata. El LLM se sustituye por `FakeBackend`, que
devuelve respuestas JSON preparadas, y las funciones de red de `linking` se
reemplazan con monkeypatch.
"""

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import pytest  # noqa: E402


class FakeBackend:
    """Backend de prueba: responde según una palabra clave del system prompt."""

    model = "fake-model"

    def __init__(self, respuestas):
        # respuestas: lista de (fragmento_del_prompt, dict_de_respuesta)
        self.respuestas = respuestas
        self.llamadas = []

    def complete_json(self, system_prompt, user_prompt, num_predict=None):
        self.llamadas.append((system_prompt, user_prompt, num_predict))
        for fragmento, valor in self.respuestas:
            if fragmento in system_prompt:
                return valor
        raise AssertionError("prompt inesperado en FakeBackend")


@pytest.fixture
def fake_backend():
    return FakeBackend
