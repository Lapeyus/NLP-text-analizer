# Arquitectura

Este documento describe cómo está construido `text-analizer`: qué piezas tiene, cómo fluyen los datos entre
ellas, dónde corre cada una y qué decisiones de diseño se ven en el código. Todo lo que se afirma aquí sale del
código versionado; donde algo no se puede determinar desde el repo, se dice.

- Requisitos y contratos: [SPEC.md](SPEC.md)
- Pruebas: [TESTING.md](TESTING.md)

---

## 1. Contexto

`text-analizer` es una herramienta de línea de comandos y una pequeña librería de Python. No es un servicio: no
expone puertos ni corre en un horario. La usa una persona desarrolladora en su máquina, o un programa de Python que
importa `nlp_local` en lugar de `google.cloud.language_v1`.

```mermaid
flowchart LR
    U["Persona usuaria (terminal)"]
    P["Programa Python que importa nlp_local"]
    TA["text-analizer (CLI + librería nlp_local)"]
    GNL["Google Cloud Natural Language API v1/v2"]
    OL["Ollama (servidor local, puerto 11434)"]
    WD["Wikidata API (wbsearchentities / wbgetentities)"]
    GH["GitHub Actions (revisión de PR con LLM)"]

    U -->|"analyze.py / analyze-google / analyze-local"| TA
    P -->|"from nlp_local import language_v1"| TA
    TA -->|"backend google: HTTPS + credenciales ADC"| GNL
    TA -->|"backend local: POST /api/chat"| OL
    TA -->|"solo con --entity-linking"| WD
    GH -.->|"comenta PRs; no ejecuta el código"| TA
```

| Actor o sistema | Rol | Obligatorio |
|---|---|---|
| Google Cloud Natural Language | Motor "maestro": la referencia de compatibilidad | Solo para `--backend google`, `--compare` y el arnés de paridad |
| Ollama | Servidor de inferencia local que ejecuta el LLM | Solo para `--backend local` |
| Wikidata | Base de conocimiento para *entity linking* local | Solo con `--entity-linking` / `entity_linking=True` |
| GitHub Actions | Revisión automática de pull requests con un LLM externo | No; no afecta la ejecución |

---

## 2. Componentes

```mermaid
flowchart TB
    subgraph CLI["Ejecutables (raíz del repo)"]
        AG["analyze-google"]
        AL["analyze-local"]
        AN["analyze.py: argparse, crear_cliente(), _ejecutar(), formateadores, _comparar()"]
        MA["main.py: referencia comentada de los 12 análisis (solo Google)"]
    end

    subgraph NLP["Paquete nlp_local"]
        INIT["__init__.py: fachada language_v1 (_CompatV1)"]
        CLIENT["client.py: LanguageServiceClient"]
        PROMPTS["prompts.py: prompts y vocabularios"]
        BACKEND["backend.py: OllamaBackend, CachingBackend, _extract_json"]
        CONVERT["convert.py: JSON del modelo a protos de Google"]
        LINK["linking.py: EntityLinker (Wikidata)"]
    end

    subgraph PAR["Paquete parity"]
        CORPUS["corpus.py: 20 textos + PLAN"]
        HARNESS["harness.py: corre maestro y alumno, CacheGoogle"]
        METRICS["metrics.py: una métrica 0-100 por análisis"]
    end

    GLIB["google-cloud-language (protos y cliente oficial)"]

    AG --> AN
    AL --> AN
    AN -->|"backend google"| GLIB
    AN -->|"backend local"| CLIENT
    MA --> GLIB
    INIT --> CLIENT
    INIT -.->|"reexporta enums y tipos"| GLIB
    CLIENT --> PROMPTS
    CLIENT --> BACKEND
    CLIENT --> CONVERT
    CLIENT --> LINK
    CONVERT --> GLIB
    LINK --> BACKEND
    HARNESS --> CORPUS
    HARNESS --> METRICS
    HARNESS --> CLIENT
    HARNESS --> GLIB
```

### 2.1 Ejecutables

| Archivo | Responsabilidad |
|---|---|
| `analyze.py` | CLI unificado. `crear_cliente(backend, model, entity_linking)` es el **único** punto donde se distingue el motor; `_ejecutar()` despacha el análisis y los `_f_*` formatean la respuesta como líneas de texto. `--compare` corre los dos motores y los imprime lado a lado. También es el *entry point* `analyze` del paquete (`pyproject.toml`). |
| `analyze-google`, `analyze-local` | Atajos de 24 líneas: quitan cualquier `--backend` de `argv` y añaden `--backend google` o `--backend local` antes de llamar a `analyze.main`. |
| `main.py` | Material de referencia: 12 funciones `sample_*` documentadas (cuándo usar cada análisis, cuándo no, salidas reales). Usa la API de Google (`language_v1` y `language_v2`) y detecta un `key.json` junto al archivo. No lo importa ningún otro módulo. |
| `setup.sh` | Crea `.venv`, instala dependencias (`--dev` para las de desarrollo) y comprueba si Ollama y las credenciales de Google están disponibles. |

### 2.2 `nlp_local`: reemplazo directo del cliente de Google

| Módulo | Qué hace | Detalles relevantes |
|---|---|---|
| `__init__.py` | Exporta `LanguageServiceClient`, `OllamaBackend`, `CachingBackend`, `BackendError` y `language_v1`. | `language_v1` es un objeto `_CompatV1` que delega todo atributo en `google.cloud.language_v1` salvo `LanguageServiceClient`, así que enums y tipos son **los mismos objetos** que los de Google. |
| `client.py` | `LanguageServiceClient` con los métodos `analyze_sentiment`, `analyze_entities`, `analyze_entity_sentiment`, `classify_text`, `moderate_text`, `analyze_syntax`, `annotate_text`. | Acepta `request` como dict o proto, o `document` suelto; ignora `credentials=`, `client_options=`, etc. Desmarca HTML antes de enviarlo al modelo. Rechaza `gcs_content_uri` con `NotImplementedError`. Replica el `InvalidArgument` de Google en `classify_text` con menos de 20 tokens. Detecta idioma es/en con una heurística si el documento no lo declara. |
| `prompts.py` | Un *system prompt* por análisis más vocabularios (`ENTITY_TYPES`, `POS_TAGS`, `DEP_LABELS_COMUNES`, `MODERATION_CATEGORIES`, `CLASSIFICATION_TOP_LEVEL`) y `CLASSIFY_MIN_TOKENS = 20`. | Las reglas de calibración salen de comparar contra Google (por ejemplo, "nunca 0.0 exacto" en sentimiento, `store` es `LOCATION`). |
| `backend.py` | `OllamaBackend.complete_json()` llama a `POST {OLLAMA_HOST}/api/chat` y devuelve un dict. `CachingBackend` lo envuelve con caché en disco. | `think=False`, `temperature=0.0`, `num_predict=1024` (4096 para sintaxis), `timeout=300`, `retries=2`. Ante JSON inválido reintenta endureciendo la instrucción; ante error de red falla de inmediato con `BackendError`. `_extract_json` tolera bloques markdown y prosa alrededor. |
| `convert.py` | Construye los protos reales (`AnalyzeSentimentResponse`, etc.) desde el JSON del modelo. | Aplica las "leyes" medidas contra Google: score de documento = media de oraciones, magnitude = suma de \|score\| × 1.05, salience normalizada a suma 1.0. Calcula offsets buscando cada fragmento en el texto. Tolera enums inventados, campos ausentes e índices fuera de rango. No emite MIDs del modelo (`EMITIR_MIDS = False`). |
| `linking.py` | `EntityLinker.enlazar(nombre, contexto, es_propio)` devuelve `(mid, wikipedia_url)`. | Solo enlaza nombres propios. Candidatos de Wikidata → el LLM elige un índice (o -1) → `P646` (Freebase MID, el primero) y sitelink `enwiki`. Pausa mínima de 0.6 s entre llamadas, reintentos ante 429 y contadores separados `enlazadas` / `rechazadas` / `fallos_red`. |

### 2.3 `parity`: arnés de paridad

| Módulo | Qué hace |
|---|---|
| `corpus.py` | 20 textos con clave (`sent-*`, `ent-*`, `clas-*`, `mod-*`, `syn-*`, `es-*`) y `PLAN`, que decide qué análisis corre sobre qué prefijos. |
| `harness.py` | `evaluar()` corre Google y `nlp_local` sobre cada texto del plan, compara también las excepciones (`_comparar_errores`) y reporta una media 0-100 por análisis y una global. `CacheGoogle` guarda las respuestas del maestro en `parity/.cache_google.json`. |
| `metrics.py` | Una función por análisis (`sentiment`, `entities`, `classify`, `moderate`, `syntax`) que devuelve métricas crudas y un `score` ponderado por lo que notaría un consumidor. |

---

## 3. Flujos de datos

### 3.1 Análisis con el backend local

```mermaid
sequenceDiagram
    autonumber
    participant U as Usuario
    participant CLI as analyze.py
    participant C as nlp_local LanguageServiceClient
    participant K as CachingBackend
    participant O as OllamaBackend
    participant OL as Ollama /api/chat
    participant V as convert.py

    U->>CLI: analyze-local sentiment "texto"
    CLI->>CLI: argparse, leer texto (argumento, --file o stdin)
    CLI->>C: crear_cliente("local", model, entity_linking)
    CLI->>C: analyze_sentiment(request con document)
    C->>C: _desempaquetar y _texto_de (HTML a texto plano)
    C->>K: complete_json(prompts.SENTIMENT, "Texto: ...")
    alt clave en nlp_local/.cache_llm.json
        K-->>C: dict memorizado
    else sin caché
        K->>O: complete_json(...)
        O->>OL: POST model, messages, think=false, temperature=0
        OL-->>O: message.content (texto)
        O->>O: _extract_json, reintento si no es JSON
        O-->>K: dict
        K->>K: guardar en disco (escritura atómica)
        K-->>C: dict
    end
    C->>V: sentimiento(datos, texto, idioma)
    V-->>C: AnalyzeSentimentResponse (proto de Google)
    C-->>CLI: respuesta
    CLI-->>U: "== sentiment · local (modelo) ==" y líneas formateadas
```

Con el backend `google` el flujo es el mismo hasta `crear_cliente`, que devuelve
`google.cloud.language_v1.LanguageServiceClient()`; desde ahí `_ejecutar()` y los formateadores son idénticos.

### 3.2 Entity linking local contra Wikidata

```mermaid
sequenceDiagram
    autonumber
    participant C as LanguageServiceClient
    participant V as convert._construir_entidades
    participant L as EntityLinker
    participant W as Wikidata API
    participant M as LLM (backend)

    C->>M: prompts.ENTITIES
    M-->>C: entities con name, type, weight, proper
    C->>V: entidades(datos, texto, idioma, linker)
    loop por cada entidad
        V->>L: enlazar(nombre, texto, proper)
        alt nombre común o menor a 2 caracteres
            L-->>V: (None, None)
        else nombre propio
            L->>W: wbsearchentities (o caché .cache_wikidata.json)
            W-->>L: hasta 5 candidatos (QID, etiqueta, descripción)
            alt un candidato con etiqueta idéntica
                L->>L: índice 0 sin consultar al modelo
            else varios candidatos
                L->>M: prompt DESAMBIGUAR con la frase y la lista
                M-->>L: index (o -1 si ninguno encaja)
            end
            L->>W: wbgetentities del QID elegido
            W-->>L: claims P646 y sitelink enwiki
            L-->>V: (primer MID, URL de Wikipedia)
        end
        V->>V: metadata mid y wikipedia_url si existen
    end
    V-->>C: entidades ordenadas por salience
```

### 3.3 Arnés de paridad

```mermaid
sequenceDiagram
    autonumber
    participant H as parity.harness
    participant CG as CacheGoogle
    participant G as Google NL API
    participant L as nlp_local
    participant MT as parity.metrics

    loop por análisis pedido y texto de corpus.textos_para()
        H->>CG: obtener(analisis, clave)
        alt en parity/.cache_google.json
            CG-->>H: proto reconstruido desde JSON
        else
            CG->>G: llamada real (consume cuota)
            G-->>CG: respuesta
            CG-->>H: respuesta (y la guarda)
        end
        H->>L: misma llamada con nlp_local
        L-->>H: respuesta o excepción
        alt alguna de las dos falló
            H->>H: _comparar_errores (100 si mismo tipo de error)
        else ambas respondieron
            H->>MT: métrica del análisis
            MT-->>H: dict con score 0-100
        end
    end
    H-->>H: media por análisis, GLOBAL, estadísticas de caché y tiempo
```

---

## 4. Modelo de despliegue

No hay despliegue en servidores. Todo corre en la máquina de quien lo usa:

```mermaid
flowchart LR
    subgraph Local["Máquina de la persona usuaria"]
        VENV[".venv con Python 3.10+ y google-cloud-language"]
        REPO["Repo: analyze.py, nlp_local, parity"]
        CACHE["Cachés en disco (ignoradas por git)"]
        OLL["Ollama en localhost:11434 con un modelo descargado"]
        ADC["Credenciales ADC de gcloud o key.json"]
    end
    subgraph Nube["Servicios externos"]
        GNL["Google Cloud Natural Language"]
        WD["Wikidata"]
    end
    REPO --> VENV
    REPO --> CACHE
    REPO -->|"HTTP local"| OLL
    REPO -->|"HTTPS con ADC"| GNL
    ADC -.-> GNL
    REPO -->|"HTTPS, opcional"| WD
```

| Pieza | Dónde vive | Notas |
|---|---|---|
| Código | Clon del repo | Se ejecuta desde la raíz, o instalado con `pip install -e .` (crea el comando `analyze`). |
| Entorno | `.venv/` | Lo crea `setup.sh`; está en `.gitignore`. |
| Cachés | `nlp_local/.cache_llm.json`, `nlp_local/.cache_wikidata.json`, `parity/.cache_google.json` | Ignoradas por git. Se pueden borrar sin perder nada más que tiempo o cuota. |
| Credenciales de Google | ADC de `gcloud` o `key.json` en la raíz | `key.json` está en `.gitignore`. Solo `main.py` lo detecta automáticamente (ver SPEC, RNF-05). |
| Modelo local | Ollama | El default es `qwen3.8:27b-mlx` (`backend.py`), sobreescribible con `NLP_LOCAL_MODEL` o `--model`. |

---

## 5. Dependencias externas y cuotas

| Dependencia | Uso | Límites o costo conocidos en el código |
|---|---|---|
| `google-cloud-language>=2.13.0,<3.0.0` | Cliente oficial y **protos de respuesta**, que `nlp_local` reutiliza aunque no llame a Google | El piso 2.13 es por `moderate_text` y `language_v2`. |
| `six>=1.16.0` | Una línea de `main.py` (`six.binary_type`) | Vestigio de Python 2, documentado en `requirements.txt`. |
| Google Cloud Natural Language | Backend `google` | Facturable por feature y por cada 1.000 caracteres; el arnés cachea las respuestas para no repetir llamadas. |
| Ollama | Backend `local` | Sin cuota; latencia de ~1 a 15 s por llamada según el modelo (medido por el autor, ver README). |
| Wikidata | *Entity linking* local | Devuelve 429 si se consulta seguido: pausa de 0.6 s, hasta 5 reintentos con espera creciente y `User-Agent` configurable (`NLP_LOCAL_USER_AGENT`). |

Fuera de esas dos dependencias de Python, todo el código usa la biblioteca estándar (`json`, `os`, `re`, `time`,
`urllib`).

---

## 6. Decisiones de diseño

Estas decisiones se ven en el código y en sus comentarios:

1. **Reutilizar los protos de Google en vez de definir tipos propios** (`convert.py`). La compatibilidad es exacta
   por construcción: `isinstance(r, language_v1.AnalyzeEntitiesResponse)` es cierto y los enums comparan igual. El
   costo es depender de `google-cloud-language` incluso para el motor local.
2. **Un solo punto de selección de motor** (`analyze.crear_cliente`). Después de construir el cliente no hay
   ninguna rama por backend; `analyze.py` existe en parte para demostrarlo.
3. **Pedirle al modelo solo cantidades primitivas y derivar el resto** (`convert.sentimiento`,
   `_construir_entidades`). El score del documento, la magnitude y la salience se calculan aritméticamente porque
   el modelo devolvía valores fuera de escala.
4. **No inventar identificadores**. Los MID que propone el modelo se descartan (`EMITIR_MIDS = False`); el
   *entity linking* solo existe vía Wikidata, donde el modelo elige entre candidatos reales.
5. **Replicar también los errores de Google**: `classify_text` lanza `InvalidArgument` con el mismo mensaje por
   debajo de 20 tokens, y el arnés puntúa "los dos fallan igual" como paridad.
6. **Determinismo y caché**: `temperature=0.0` y caché en disco con clave `[modelo, prompt, texto, num_predict]`,
   de modo que editar un prompt invalida solo lo afectado.
7. **Sin dependencia de decodificación por gramática**: los modelos MLX de Ollama ignoran `format`, así que el
   JSON se extrae del texto (`_extract_json`) y se reintenta si no es válido.
8. **Funcionar sin conexión por defecto**: el *entity linking* (que necesita red) va apagado salvo que se pida.
