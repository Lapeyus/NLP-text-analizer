# Pruebas

Cómo se verifica `text-analizer`, cómo correr las pruebas y qué queda sin cubrir. Los requisitos referidos (RF-xx)
están en [SPEC.md](SPEC.md).

---

## 1. Estrategia

El proyecto tiene dos niveles de verificación con objetivos distintos:

| Nivel | Qué responde | Necesita | Dónde |
|---|---|---|---|
| **Pruebas unitarias offline** | ¿La lógica propia (CLI, conversión a protos, extracción de JSON, caché, *linking*, métricas) hace lo que dice la especificación? | Solo el `.venv` | `tests/` |
| **Arnés de paridad** | ¿Cuánto se parece el motor local a la API de Google sobre un corpus real? | Credenciales de Google, Ollama con un modelo y cuota | `parity/` |

Las pruebas unitarias **no** llaman a ningún servicio externo:

- El LLM se reemplaza por `FakeBackend` (`tests/conftest.py`), que devuelve JSON preparado según el *system
  prompt* que recibe. Se inyecta con `LanguageServiceClient(backend=...)`, que es el punto de extensión que el
  propio cliente ofrece.
- Ollama se simula parcheando `OllamaBackend._chat` o `urllib.request.urlopen`.
- Wikidata se simula parcheando `linking.buscar_candidatos`, `linking.resolver` y `linking._http`.
- La API de Google no se invoca: en `test_cli.py` se reemplaza `analyze.crear_cliente`, y las métricas de paridad
  se prueban con protos construidos a mano.
- Las cachés en disco se redirigen a `tmp_path`; las pruebas no escriben en `nlp_local/.cache_*.json` ni en
  `parity/.cache_google.json`.

---

## 2. Cómo correrlas

Desde la raíz del repo, con el entorno creado por `./setup.sh --dev` (instala `pytest`, `black` y `ruff`):

```bash
.venv/bin/python -m pytest
```

```bash
.venv/bin/python -m pytest tests/test_convert.py -v
```

Estilo (configurado en `pyproject.toml`: `E`, `F`, `W`, `I`, sin `E501`):

```bash
.venv/bin/ruff check .
```

Arnés de paridad (consume cuota de Google y tiempo de inferencia; no es parte de la suite automática):

```bash
python -m parity.harness
```

```bash
python -m parity.harness sentiment entities --model qwen3.5:9b-mlx
```

---

## 3. Inventario de suites

| Archivo | Pruebas | Qué cubre | Requisitos |
|---|---:|---|---|
| `tests/conftest.py` | — | Agrega la raíz del repo a `sys.path` y define `FakeBackend`. | — |
| `tests/test_backend.py` | 14 | `_extract_json` (JSON directo, bloque markdown, prosa alrededor con llaves dentro de cadenas, arrays, vacío, sin JSON); `OllamaBackend` (valores por defecto, reintento con instrucción reforzada, agotamiento de reintentos, error de red sin reintento, cuerpo exacto de `POST /api/chat`); `CachingBackend` (aciertos y fallos, persistencia en disco, clave que incluye el prompt, caché corrupta). | RNF-03, RNF-04, RNF-06 |
| `tests/test_convert.py` | 14 | Leyes de sentimiento (media, Σ\|score\| × 1.05), redondeo y acotado, texto sin oraciones, offset -1; entidades (salience suma 1, orden, tipos desconocidos a `OTHER`, menciones propias/comunes, supresión de MID, `linker` simulado, sentimiento por entidad); clasificación; las 16 categorías de moderación; sintaxis con `head` inválido; `anotacion`. | RF-08 a RF-11, RF-13, RF-14, RF-19 |
| `tests/test_client.py` | 15 | Fachada `language_v1` con los tipos reales de Google; `kwargs` de Google ignorados; respuesta como proto; `request` como proto y `document` suelto; `document` ausente; `gcs_content_uri` no soportado; HTML desmarcado; umbral de 20 tokens en `classify_text` (19 falla sin llamar al modelo, 20 pasa); conteo de tokens; detección de idioma; techo de 4096 tokens en sintaxis; `annotate_text` con una llamada por feature. | RF-06 a RF-08, RF-12, RF-15, RF-16, RF-19 |
| `tests/test_linking.py` | 8 | `EntityLinker`: sustantivos comunes no se buscan; candidato único idéntico sin modelo; rechazo cuando la etiqueta no coincide; el modelo elige índice (con `num_predict=64`); el modelo dice -1; fallo de red en búsqueda o resolución contado en `fallos_red`; `resolver` toma el primer `P646`, arma la URL de Wikipedia como Google y usa la caché. | RF-17, RF-18, RNF-07 |
| `tests/test_parity_metrics.py` | 12 | Métrica de sentimiento (identidad, tolerancia de ±0.2 sin "acantilado", signo invertido); emparejamiento de entidades por contención y F1; clasificación (ambas vacías, ruta corta con nivel 1 correcto); moderación; sintaxis con tokenización distinta; corpus de 20 claves únicas y `PLAN` coherente con `harness.ANALISIS`; `_comparar_errores`. | RF-20, RF-21 |
| `tests/test_cli.py` | 14 | Lista de análisis y backends; `_doc`; formateadores (`MIXTO`, umbral de moderación, `link`); `_seguro`; `main` con backend local, `--file`, stdin, texto vacío (código 2) y análisis inválido; `--compare` lado a lado; atajos `analyze-google` / `analyze-local` (ejecutados con `runpy`). | RF-01 a RF-05 |

**Total: 77 pruebas.**

### Resultado real

Corrida del 2026-10-25 en macOS (Apple Silicon) con `.venv` (Python 3.12.13, `google-cloud-language` 2.21.0,
`pytest` 9.1.1):

```
$ .venv/bin/python -m pytest -q
77 passed in 0.25s
```

`ruff check tests` pasa sin hallazgos. `ruff check .` reporta 7 hallazgos **preexistentes** en código de
producción (6 × `E741` por variables llamadas `l` en `analyze.py` y `parity/metrics.py`, y 1 × `I001` por el
orden de imports de `main.py`); no se modificaron.

Antes de esta suite el repo no tenía pruebas automatizadas: `pytest` no encontraba ninguna (`no tests ran`) y el
único mecanismo de verificación era el arnés de paridad, que requiere servicios externos.

---

## 4. Integración continua (`.github/workflows`)

Hay dos workflows, y **ninguno ejecuta las pruebas**:

| Archivo | Nombre | Disparadores | Qué hace |
|---|---|---|---|
| `main.yml` | `Code Review` | `pull_request` y `pull_request_review_comment` (creado) | Corre la acción `fluxninja/openai-pr-reviewer@main` en `ubuntu-latest`, que revisa el diff del PR con un modelo de OpenAI y deja comentarios. |
| `openai-pr-reviewer.yml` | `Code Review` | Los mismos | **Idéntico** a `main.yml`. |

Detalles comunes a ambos:

- Permisos: `contents: read`, `pull-requests: write`.
- Secretos que espera: `GITHUB_TOKEN` (automático) y `OPENAI_API_KEY` (secreto del repositorio; su valor no está
  en el repo).
- Opciones: `debug: false`, `review_comment_lgtm: false`.
- `concurrency` agrupa por repositorio, número de PR y tipo de evento, y cancela corridas previas salvo en
  comentarios de revisión.
- La acción se referencia por rama (`@main`), no por versión fija.

Consecuencias: cada PR dispara dos revisiones iguales, y un PR desde un *fork* no recibe el secreto
`OPENAI_API_KEY`, por lo que la revisión fallaría ahí. No hay un job que corra `pytest` o `ruff`.

Un job mínimo para correr la suite tendría esta forma (propuesta, no versionada):

```yaml
- uses: actions/setup-python@v5
  with: {python-version: "3.12"}
- run: pip install -r requirements-dev.txt
- run: python -m pytest -q
```

---

## 5. Brechas conocidas

| Brecha | Motivo | Cómo se podría cubrir |
|---|---|---|
| Calidad real del motor local frente a Google | Requiere Google, Ollama y cuota | `python -m parity.harness` manual; publicar el resultado con modelo y fecha. |
| `OllamaBackend` contra un servidor real | Sin red en las pruebas | Prueba de integración marcada y saltada si `localhost:11434` no responde. |
| `buscar_candidatos` y `_http` (reintentos ante 429, pausa) | Dependen del reloj y de la red | Parchear `time.sleep` y `urllib.request.urlopen` con respuestas 429 simuladas. |
| `parity.harness.evaluar` de punta a punta | Construye el cliente real de Google (exige credenciales) | Parchear `G.LanguageServiceClient` y precargar `CacheGoogle`. |
| `main.py` | Cada función es una llamada facturable a Google | Fuera de alcance: es material de referencia. |
| `setup.sh` | Script de shell con efectos sobre `.venv` | Prueba manual o `shellcheck`. |
| Formateadores `_f_syntax`, `_f_classify`, `_f_annotate` | Cubiertos solo de forma indirecta | Pruebas con protos construidos a mano, como las de `_f_link`. |
