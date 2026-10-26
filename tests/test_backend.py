"""Pruebas de nlp_local.backend: extracción de JSON, reintentos y caché."""

import json
import urllib.error

import pytest

from nlp_local import backend
from nlp_local.backend import BackendError, CachingBackend, OllamaBackend, _extract_json


class TestExtractJson:
    def test_json_directo(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_bloque_markdown(self):
        assert _extract_json('```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}

    def test_prosa_antes_y_despues(self):
        texto = 'Claro, aquí va: {"x": "llave } dentro"} espero que sirva'
        assert _extract_json(texto) == {"x": "llave } dentro"}

    def test_array(self):
        assert _extract_json("resultado: [1, 2, 3]") == [1, 2, 3]

    def test_vacio(self):
        with pytest.raises(BackendError):
            _extract_json("")

    def test_sin_json(self):
        with pytest.raises(BackendError):
            _extract_json("no hay nada aquí")


class TestOllamaBackend:
    def test_defaults(self):
        b = OllamaBackend(model="m", host="http://h:1/")
        assert b.model == "m"
        assert b.host == "http://h:1"
        assert b.think is False
        assert b.temperature == 0.0

    def test_reintenta_ante_json_invalido(self, monkeypatch):
        b = OllamaBackend(model="m", retries=2)
        salidas = iter(["prosa sin json", '{"ok": true}'])
        sistemas = []

        def fake_chat(sistema, usuario, num_predict=None):
            sistemas.append(sistema)
            return next(salidas)

        monkeypatch.setattr(b, "_chat", fake_chat)
        assert b.complete_json("SYS", "USR") == {"ok": True}
        assert len(sistemas) == 2
        assert "IMPORTANTE" in sistemas[1]

    def test_agota_reintentos(self, monkeypatch):
        b = OllamaBackend(model="m", retries=1)
        monkeypatch.setattr(b, "_chat", lambda *a, **k: "nada")
        with pytest.raises(BackendError):
            b.complete_json("SYS", "USR")

    def test_error_de_red_no_reintenta(self, monkeypatch):
        b = OllamaBackend(model="m", retries=3)
        n = []

        def falla(*a, **k):
            n.append(1)
            raise urllib.error.URLError("conexión rechazada")

        monkeypatch.setattr(b, "_chat", falla)
        with pytest.raises(BackendError, match="no se pudo contactar a Ollama"):
            b.complete_json("SYS", "USR")
        assert len(n) == 1

    def test_cuerpo_de_la_peticion(self, monkeypatch):
        b = OllamaBackend(model="m", host="http://h:1", num_predict=99)
        capturado = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, *a):
                return json.dumps({"message": {"content": '{"r": 1}'}}).encode()

        def fake_urlopen(req, timeout=None):
            capturado["url"] = req.full_url
            capturado["cuerpo"] = json.loads(req.data)
            return Resp()

        monkeypatch.setattr(backend.urllib.request, "urlopen", fake_urlopen)
        assert b.complete_json("SYS", "USR") == {"r": 1}
        assert capturado["url"] == "http://h:1/api/chat"
        c = capturado["cuerpo"]
        assert c["model"] == "m" and c["stream"] is False and c["think"] is False
        assert c["options"] == {"temperature": 0.0, "num_predict": 99}
        assert b.calls == 1


class TestCachingBackend:
    def test_memoriza_y_persiste(self, tmp_path, fake_backend):
        inner = fake_backend([("SYS", {"v": 1})])
        ruta = str(tmp_path / "cache.json")
        c = CachingBackend(inner, path=ruta)
        assert c.complete_json("SYS", "a") == {"v": 1}
        assert c.complete_json("SYS", "a") == {"v": 1}
        assert (c.hits, c.misses) == (1, 1)
        assert len(inner.llamadas) == 1

        # Una instancia nueva lee la caché del disco.
        c2 = CachingBackend(fake_backend([]), path=ruta)
        assert c2.complete_json("SYS", "a") == {"v": 1}
        assert c2.hits == 1

    def test_clave_incluye_prompt(self, tmp_path, fake_backend):
        inner = fake_backend([("SYS", {"v": 1})])
        c = CachingBackend(inner, path=str(tmp_path / "c.json"))
        c.complete_json("SYS", "a")
        c.complete_json("SYS v2", "a")
        assert c.misses == 2
        assert c.model == "fake-model"

    def test_cache_corrupta_se_ignora(self, tmp_path, fake_backend):
        ruta = tmp_path / "c.json"
        ruta.write_text("no es json")
        c = CachingBackend(fake_backend([("S", {})]), path=str(ruta))
        assert c.complete_json("S", "x") == {}
