#!/usr/bin/env bash
#
# Crea el entorno virtual e instala las dependencias.
#
#     ./setup.sh              # entorno de ejecución
#     ./setup.sh --dev        # además, herramientas de desarrollo
#     ./setup.sh --python python3.12
#
# Al terminar comprueba el entorno y te dice exactamente qué falta para cada
# uno de los dos motores, en vez de dejarte descubrirlo en el primer error.
#
# Es idempotente: si el entorno ya existe, actualiza las dependencias.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

VENV=".venv"
DEV=0
PY=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dev) DEV=1; shift ;;
        --python) PY="${2:-}"; shift 2 ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Opción desconocida: $1 (usa --help)" >&2; exit 2 ;;
    esac
done

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
# 1. Elegir intérprete
# ---------------------------------------------------------------------------
# Se exige 3.10+. En 3.9 el proyecto arranca, pero google-cloud-language emite
# un FutureWarning en CADA import avisando de que dejó de soportarla, y la
# salida se vuelve ilegible.
elegir_python() {
    if [[ -n "$PY" ]]; then
        command -v "$PY" >/dev/null 2>&1 || { echo "No existe: $PY" >&2; exit 1; }
        echo "$PY"; return
    fi
    for c in python3.12 python3.11 python3.13 python3.10 python3.14 python3; do
        if command -v "$c" >/dev/null 2>&1 &&
           "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
            echo "$c"; return
        fi
    done
    echo ""
}

say "Buscando un Python 3.10 o superior"
PYBIN="$(elegir_python)"
if [[ -z "$PYBIN" ]]; then
    echo "  No se encontró Python 3.10+." >&2
    echo "  En macOS:  brew install python@3.12" >&2
    echo "  Luego:     ./setup.sh --python python3.12" >&2
    exit 1
fi
ok "$PYBIN ($("$PYBIN" --version 2>&1))"

# ---------------------------------------------------------------------------
# 2. Entorno virtual
# ---------------------------------------------------------------------------
if [[ -d "$VENV" ]]; then
    say "El entorno $VENV ya existe; se actualiza"
else
    say "Creando el entorno virtual en $VENV"
    "$PYBIN" -m venv "$VENV"
fi
ok "$VENV listo"

# Se usan las rutas del venv directamente en vez de `source activate`: este
# script corre en una subshell, así que activar aquí no afectaría a tu terminal.
VPY="$VENV/bin/python"
VPIP="$VENV/bin/pip"

say "Actualizando pip"
"$VPY" -m pip install --quiet --upgrade pip setuptools wheel
ok "pip $("$VPIP" --version | awk '{print $2}')"

# ---------------------------------------------------------------------------
# 3. Dependencias
# ---------------------------------------------------------------------------
if [[ $DEV -eq 1 ]]; then
    say "Instalando dependencias de ejecución y desarrollo"
    "$VPIP" install --quiet -r requirements-dev.txt
else
    say "Instalando dependencias de ejecución"
    "$VPIP" install --quiet -r requirements.txt
fi
ok "$("$VPIP" show google-cloud-language | awk '/^Version/{print "google-cloud-language " $2}')"

# ---------------------------------------------------------------------------
# 4. Comprobaciones
# ---------------------------------------------------------------------------
say "Comprobando el entorno"

"$VPY" - <<'PY'
import sys
try:
    from google.cloud import language_v1, language_v2  # noqa: F401
    import nlp_local  # noqa: F401
    print("  \033[32m✓\033[0m google-cloud-language y nlp_local importan bien")
except Exception as e:
    print("  \033[31m✗\033[0m fallo al importar: {}: {}".format(type(e).__name__, e))
    sys.exit(1)
PY

# --- motor local: Ollama ---
if curl -sf --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
    MODELOS=$(curl -sf --max-time 3 http://localhost:11434/api/tags |
              "$VPY" -c 'import json,sys; print(len(json.load(sys.stdin).get("models",[])))' 2>/dev/null || echo 0)
    if [[ "$MODELOS" -gt 0 ]]; then
        ok "Ollama activo con $MODELOS modelo(s)  →  ./analyze-local ya funciona"
    else
        warn "Ollama activo pero SIN modelos.  Descarga uno:  ollama pull qwen3:32b"
    fi
else
    warn "Ollama no responde en localhost:11434 (necesario para ./analyze-local)"
    echo "      Instálalo desde https://ollama.com y luego:  ollama pull qwen3:32b"
fi

# --- motor de Google ---
if command -v gcloud >/dev/null 2>&1; then
    if [[ -f "$HOME/.config/gcloud/application_default_credentials.json" ]] || [[ -f key.json ]]; then
        ok "Credenciales de Google presentes  →  ./analyze-google ya funciona"
        echo "      Recuerda habilitar la API:  gcloud services enable language.googleapis.com"
    else
        warn "gcloud instalado pero sin credenciales (necesario para ./analyze-google)"
        echo "      gcloud auth application-default login"
    fi
else
    warn "gcloud no está instalado (necesario para ./analyze-google)"
    echo "      https://cloud.google.com/sdk/docs/install"
fi

# ---------------------------------------------------------------------------
# 5. Cómo seguir
# ---------------------------------------------------------------------------
cat <<EOF

$(say "Listo. Activa el entorno con:")

    source $VENV/bin/activate

Y pruébalo:

    ./analyze-local  sentiment "The food was amazing but the service was terrible."
    ./analyze-google sentiment "The food was amazing but the service was terrible."
    ./analyze.py --compare sentiment "Great price. Awful build quality."

Para salir del entorno:  deactivate
EOF
