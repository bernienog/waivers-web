#!/usr/bin/env bash
# start.sh — waivers-project en un paso (Linux/macOS).
#   Uso:  ./start.sh            # prod: UI+API en :3001, abre el navegador
#         ./start.sh --dev      # dev: vite en :5173
#         ./start.sh --no-browser
#Es el gemelo de start.ps1: mismas decisiones, en bash. La UI se sirve en
# 127.0.0.1:3001, o sea que esto es el modo navegador (no hay app de
# escritorio para Linux todavia; ver LINUX.md).
set -euo pipefail

DEV=0
NO_BROWSER=0
# `${@+"$@"}` en vez de "$@": con `set -u`, el bash 3.2 que trae macOS
# de serie truena al Expandir "$@" sin argumentos.
for a in ${@+"$@"}; do
  case "$a" in
    -Dev|--dev) DEV=1 ;;
    -NoBrowser|--no-browser) NO_BROWSER=1 ;;
    *) echo "opcion desconocida: $a" >&2; exit 1 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PORT=3001

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Falta $1. $2" >&2; exit 1; }
}
need python3 "Instala Python 3.12+ (apt install python3 python3-venv)."
if [ "$DEV" -eq 0 ]; then need npm "Instala Node LTS."; fi

# .env: si falta, crear desde el ejemplo y avisar (sin JWT no hay writers).
if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo ".env creado desde el ejemplo: conectate con Sleeper y re-corre." >&2
fi

# venv + deps python.
VPY="$ROOT/.venv/bin/python"
if [ ! -x "$VPY" ]; then
  echo "Creando .venv..."
  python3 -m venv "$ROOT/.venv"
  "$VPY" -m pip install -q -r "$ROOT/requirements.txt"
else
  if ! "$VPY" -c "import fastapi" 2>/dev/null; then
    echo "Instalando deps python..."
    "$VPY" -m pip install -q -r "$ROOT/requirements.txt"
  fi
fi

# Fantasma en :3001: un server viejo = codigo viejo.
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" 2>/dev/null || true
elif command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
  if [ -n "$PIDS" ]; then kill $PIDS 2>/dev/null || true; fi
fi
sleep 1

if [ "$DEV" -eq 1 ]; then
  if [ ! -d "$ROOT/web/node_modules" ]; then
    echo "npm install (una vez)..."
    npm --prefix "$ROOT/web" install
  fi
  "$VPY" -m uvicorn api.main:app --port "$PORT" &
  npm --prefix "$ROOT/web" run dev &
  URL="http://localhost:5173/"
else
  # Prod: compilar la UI si falta. Ojo: `web/dist` esta en .gitignore,
  # asi que en un clon recien hecho hace falta Node (una vez).
  if [ ! -f "$ROOT/web/dist/index.html" ]; then
    echo "Compilando frontend..."
    if [ ! -d "$ROOT/web/node_modules" ]; then npm --prefix "$ROOT/web" install; fi
    npm --prefix "$ROOT/web" run build
    if [ ! -f "$ROOT/web/dist/index.html" ]; then
      echo "Fallo el build del frontend." >&2; exit 1
    fi
  fi
  "$VPY" -m uvicorn api.main:app --port "$PORT" &
  URL="http://localhost:$PORT/"
fi

# Gate: esperar /api/health hasta 60s, luego abrir el navegador.
echo "Esperando backend..."
ok=0
for _ in $(seq 1 60); do
  if curl -sf -m 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null | grep -q '"ok":true'; then
    ok=1; break
  fi
  sleep 1
done
if [ "$ok" -ne 1 ]; then
  echo "El backend no respondio en :$PORT. Revisa la salida de uvicorn." >&2
  exit 1
fi
echo "Listo: $URL"
if [ "$NO_BROWSER" -eq 0 ]; then
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
  elif command -v open >/dev/null 2>&1; then open "$URL"
  else echo "Abre $URL a mano."; fi
fi

wait
