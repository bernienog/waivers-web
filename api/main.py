"""FastAPI local (:3001). Thin wrappers sobre core/. JWT solo server-side.

Rutas writer (/claims/submit, /claims/retry, /pending/cancel) existen pero
NO se prueban en vivo sin aprobacion manual.
"""

import json as _json
import os
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_ROOT = os.path.dirname(os.path.dirname(__file__))


def _env_path():
    """Ruta del .env. Default: raíz del repo. Instalado (Tauri/sidecar):
    WAIVERS_ENV_FILE manda; si no, .env junto a WAIVERS_DATA_DIR."""
    explicit = os.environ.get("WAIVERS_ENV_FILE", "")
    if explicit:
        return explicit
    data_dir = os.environ.get("WAIVERS_DATA_DIR", "")
    if data_dir:
        return os.path.join(data_dir, ".env")
    return os.path.join(_ROOT, ".env")


try:
    from dotenv import load_dotenv
    load_dotenv(_env_path())
except ImportError:
    pass

from core import sleeper_private as priv
from core import sleeper_public as pub
from core.db import connect
from core.models import Claim
from core.validator import validate_claim
from core.week import current_week_default, resolve_leg

app = FastAPI(title="waivers-project")


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """Red de seguridad. Sin esto, una excepción que se escapa de una ruta
    sale como `Internal Server Error` (o un traceback) y el usuario ve un
    500 sin acción posible. Acá se registra completo y se responde con un
    código corto y una frase en español: el detalle va al log, no al cuerpo."""
    import traceback
    print("[interno] excepcion sin manejar: " + repr(exc), file=sys.stderr)
    traceback.print_exc()
    return JSONResponse(status_code=500,
                        content={"detail": {
                            "error": "Algo falló de nuestro lado. Cierra y vuelve a abrir la app.",
                            "code": E_INTERNAL}})

# Versión del backend (health + footer UI): la única forma de saber qué
# build corre tras un reinstall (NSIS salta archivos bloqueados).
APP_VERSION = "0.1.34"
def _uid():
    """User id dinámico (el wizard lo guarda sin reinicio)."""
    return os.environ.get("SLEEPER_USER_ID", "")

# Frontend precompilado (modo prod: `npm run build` en web/).
# Si existe, este mismo proceso sirve la UI en / (un solo puerto).
# Instalado (Tauri): WAIVERS_FRONTEND_DIR apunta al dist empaquetado
# como resource; default = web/dist del repo. Como el nivel real del
# resource varía (`_up_/` en NSIS), se prueba cada candidato y gana el
# primero con index.html (nunca crashear: landing explica si ninguno).
def _frontend_dir():
    cands = []
    env = os.environ.get("WAIVERS_FRONTEND_DIR", "")
    if env:
        cands.append(env)
        cands.append(os.path.join(env, "_up_", "dist"))
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        cands.append(os.path.join(exe_dir, "dist"))
        cands.append(os.path.join(exe_dir, "_up_", "dist"))
    cands.append(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "web", "dist"))
    for c in cands:
        if os.path.isfile(os.path.join(c, "index.html")):
            return c
    return cands[0]


_DIST = _frontend_dir()
_DIST_INDEX = os.path.join(_DIST, "index.html")


def err(msg, code=400):
    raise HTTPException(status_code=code, detail={"error": msg})


# Códigos de error: los ve la UI (para pintar español) y el usuario puede
# citarlos al pedir ayuda. El detalle crudo NUNCA viaja en la respuesta.
E_SLEEPER_READ = "sleeper-read"      # no se pudo leer de Sleeper
E_SLEEPER_WRITE = "sleeper-write"    # Sleeper rechazó una escritura
E_PROY = "proyecciones"              # no hay proyecciones de la semana
E_TOKEN = "token"                    # el token no sirvió
E_LOCAL = "local"                    # el proceso local falló (archivos)
E_FP = "fantasypros"                 # la página de FantasyPros no respondió
E_INTERNAL = "interno"               # bug nuestro: se registra, no se explica


def fail(code, msg, cause=None, status=502, dev=None):
    """Falla para el usuario. `msg` es una frase corta en español; lo que
    Sleeper o Python dijeron se queda en la consola (nunca en la respuesta:
    ahí se escapan URLs internas, rutas del .env y cuerpos crudos)."""
    if cause is not None:
        print(f"[{code}] {msg} | causa: {cause!r}", file=sys.stderr)
    raise HTTPException(status_code=status,
                        detail={"error": msg, "code": code, "dev": dev})


# Un veredicto de Sleeper es inglés limpio y lo traducimos; cualquier otra
# cosa que huela a upstream (URL, cuerpo crudo, excepción, ruta local) se
# sustituye por una frase genérica. Regex, no substrings: "requests." y
# "[Errno" no se parecían a "Exception" y se colaban.
_RE_UPSTREAM = re.compile(
    r"https?://"
    r"|\"errors\"\s*:"
    r"|\bTraceback\b"
    r"|\bexception\b"
    r"|\brequests\b"
    r"|\[Errno\b"
    r"|File \""
    r"|[A-Za-z]:\\"
    r"|%APPDATA%"
    r"|\\\\Users\\\\?",
    re.IGNORECASE)

_NOTAS_VERDICT = {
    "claimed by another owner": "se lo llevó otro dueño",
    "too many players": "la liga no tiene espacio",
    "processed successfully": "procesado",
}


def nota_para_usuario(text):
    """Convierte una nota interna (veredicto de Sleeper, cuerpo crudo de la
    mutación, repr de una excepción) en algo que sí se puede mostrar. Lo
    crudo se queda en la DB para support; esto es solo la capa de lectura."""
    t = (text or "").strip()
    if not t:
        return ""
    low = t.lower()
    for k, v in _NOTAS_VERDICT.items():
        if k in low:
            return v
    if _RE_UPSTREAM.search(t):
        return "Sleeper no lo detallo"
    if len(t) > 80:
        return t[:77] + "…"
    return t




# ---------- setup primera corrida (producto, sin ruido dev) ----------

def _resource_dir():
    """Raíz de resources empaquetados (Tauri) o raíz del repo (dev)."""
    explicit = os.environ.get("WAIVERS_RESOURCE_DIR", "")
    if explicit:
        return explicit
    return _ROOT


_EXT_SRC_CACHE = ""  # la ubicación no cambia en vida del proceso


def _ext_src():
    """Carpeta ext/ lista para sideload (manifest.json manda).
    NSIS anida `..` como `_up_/` (simple o doble según el nivel): se
    prueban los candidatos y, si no, barrido recursivo (poco profundo,
    cacheado: el onedir tiene miles de archivos)."""
    global _EXT_SRC_CACHE
    if _EXT_SRC_CACHE:
        return _EXT_SRC_CACHE
    base = _resource_dir()
    for p in (os.path.join(base, "ext"),
              os.path.join(base, "_up_", "ext"),
              os.path.join(base, "_up_", "_up_", "ext"),
              os.path.join(_ROOT, "ext")):
        if os.path.isfile(os.path.join(p, "manifest.json")):
            _EXT_SRC_CACHE = p
            return p
    for root, dirs, files in os.walk(base):
        if "manifest.json" in files and "popup.js" in files:
            _EXT_SRC_CACHE = root
            return root
        if root.count(os.sep) - base.count(os.sep) >= 4:
            dirs[:] = []
    return ""


def _windows_documents():
    """La carpeta real de "Mis documentos" segun Windows, o "" si no se sabe.

    OJO: no se usa `~\\Documents` a pelo. Con OneDrive (que es el default
    en una cuenta nueva de Windows) "Mis documentos" apunta a
    C:\\Users\\X\\OneDrive\\Documents, y la carpeta local puede ni existir.
    El explorador de archivos del usuario muestra la de OneDrive, asi que
    una carpeta creada en la local es invisible para el: el usuario no
    encuentra lo que la app le dijo que preparo. Ademas el shell (Tauri
    `document_dir()`) si sigue la redireccion, y su allowlist rechazaba
    justamente la carpeta que la app habia creado.

    FOLDERID_Documents por API: es la misma que usa Windows y la que
    respeta la redireccion, sin parsear el registro a mano.
    """
    if sys.platform != "win32":
        return ""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            FOLDERID_Documents = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
            # El GUID va como 16 bytes en orden little-endian, NO como la
            # cadena: la API lee 16 bytes del puntero y con el texto
            # "{FDD39AD0-..." se inventa un GUID distinto y falla.
            guid = ctypes.create_string_buffer(
                uuid.UUID(FOLDERID_Documents).bytes_le)
            out = wintypes.LPWSTR()          # puntero, no string
            hr = ctypes.windll.shell32.SHGetKnownFolderPath(  # type: ignore[attr-defined]
                guid, 0, None, ctypes.byref(out))
            if hr == 0 and out.value:
                try:
                    return out.value
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(out)  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        # Respaldo: lo mismo que ve el shell. "Personal" puede venir como
        # %USERPROFILE%\Documents, por eso el expandvars.
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion"
                            r"\Explorer\User Shell Folders") as k:
            val, _ = winreg.QueryValueEx(k, "Personal")
        val = os.path.expandvars(val)
        if val and os.path.isdir(val):
            return val
    except Exception:
        pass
    # Sin API ni registro: al menos lo de siempre, antes que no preparar
    # carpeta.
    return os.path.join(os.path.expanduser("~"), "Documents")


def _stage_dir():
    """Carpeta donde se copia la extensión para el sideload.

    Windows: Documents\\Waivers Connect, en la carpeta REAL de Documentos
    (ver _windows_documents). Linux/mac: Documents no existe siempre, asi
    que se va a la carpeta de datos del usuario segun XDG. Nada de crear
    un ~/Documents fantasma."""
    if sys.platform == "win32":
        docs = _windows_documents() or os.path.join(
            os.path.expanduser("~"), "Documents")
        return os.path.join(docs, "Waivers Connect")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "waivers", "extension")


@app.get("/api/setup")
def setup_state():
    """Autodiagnóstico local del producto: ¿virgen?, ¿motor?, ¿ext
    disponible/preparada?, ¿sesión?, ¿datos? Todo lectura local, cero
    llamadas a Sleeper. Es lo que muestran el wizard y 'estado'."""
    from core.db import DEFAULT_PATH, _data_dir
    staged = _stage_dir()
    jwt = _jwt_status()
    try:
        players = os.path.isfile(pub._players_cache_path())
    except Exception:
        players = False
    return {"first_run": jwt["status"] == "missing",
            "version": APP_VERSION,
            "ext_ready": bool(_ext_src()),
        "staged": os.path.isfile(os.path.join(staged, "manifest.json")),
        "stage_path": staged,
        # Donde hay que abrir el Explorador para que el usuario vea la
        # carpeta COMO CARPETA. Abrir la stage_path ensena los archivos
        # sueltos (manifest.json, popup.js...) y no hay nada que elegir:
        # "Cargar descomprimida" necesita la carpeta, no un archivo. Con
        # el padre, "Waivers Connect" aparece como un unico elemento.
        "picker_dir": os.path.dirname(staged),
        "stage_name": os.path.basename(staged),
            "jwt": jwt,
            "data_dir": _data_dir(),
            "db": DEFAULT_PATH,
            "db_exists": os.path.isfile(DEFAULT_PATH),
            "players_cached": players,
            "port": int(os.environ.get("WAIVERS_PORT", "3001")),
            "frozen": bool(getattr(sys, "frozen", False))}


@app.post("/api/setup/stage-extension")
def stage_extension():
    """Copia ext/ a Documentos/Waivers Connect + README de sideload.
    Idempotente. Así el producto no pide tocar el workspace dev."""
    import shutil
    src = _ext_src()
    if not src:
        err("fuente de la extensión no empaquetada", 500)
    dst = _stage_dir()
    try:
        os.makedirs(dst, exist_ok=True)
        for name in os.listdir(src):
            s = os.path.join(src, name)
            d = os.path.join(dst, name)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            elif os.path.isfile(s):
                shutil.copy2(s, d)
        with open(os.path.join(dst, "LEEME.txt"), "w",
                  encoding="utf-8") as f:
            # Mismo texto que el tutorial de la app, en el mismo español. Si
            # alguien abre esta carpeta en el Explorador, lo que lee aquí tiene
            # que coincidir con lo que vio arriba (antes vivía en inglés y
            # divergía).
            f.write(
                "Waivers Connect v" + APP_VERSION + "\n\n"
                "Esta es la carpeta de la extensión. Instálala una vez en\n"
                "Chrome o Edge (sin tienda, sin revisión de nadie).\n\n"
                "Si estás leyendo esto con el Explorador abierto en esta\n"
                "carpeta, cierre esa ventana: para cargarla tienes que\n"
                "elegir la CARPETA, no un archivo de adentro.\n\n"
                "1) Abre chrome://extensions (en Edge: edge://extensions).\n"
                "2) Enciende 'Modo de desarrollador' (arriba a la derecha).\n"
                "3) Pulsa 'Cargar descomprimida' (arriba a la izquierda).\n"
                "4) En el selector elige la carpeta 'Waivers Connect' (esta\n"
                "   misma), no manifest.json ni ningún archivo suelto.\n"
                "5) Fija el icono con el pin para verlo junto a la barra.\n\n"
                "Después, con la app Waivers abierta:\n"
                "  - entra a sleeper.com\n"
                "  - clic al icono Waivers Connect\n"
                "  - 'Enviar sesión a la app'\n\n"
                "El aviso de 'extensión sin verificar' del navegador es normal:\n"
                "las extensiones locales no pasan ninguna tienda.\n\n"
                "Si dice 'app cerrada', abre Waivers y repítelo.\n"
                "Si dice 'sin JWT', esa pestaña no tenía sesión: inicia sesión\n"
                "en una pestaña normal (no en incógnito).\n"
            )
    except OSError as e:
        fail(E_LOCAL, "No pude preparar la carpeta de la extensión.", e, 500)
    return {"ok": True, "path": dst}


# ---------- landing (sirve la UI precompilada si existe) ----------

@app.get("/")
def landing():
    if os.path.isfile(_DIST_INDEX):
        # no-store: la ventana Tauri reus­a la URL en cada arranque y el
        # WebView cachea agresivo (instalaciones viejas resucitan).
        return FileResponse(_DIST_INDEX, headers={"Cache-Control": "no-store"})
    return HTMLResponse("""<html><body style="background:#0b0e17;color:#e8ecf4;
      font-family:sans-serif;max-width:640px;margin:40px auto">
      <h2>waivers-project API <span style="color:#00d18f">● corriendo</span></h2>
      <p>UI sin compilar. Elige una:</p>
      <ul>
        <li>Corre <code>npm run build</code> en <code>web/</code> y recarga:
          esta misma URL servirá la UI.</li>
        <li>O levanta dev: <code>npm run dev</code> en <code>web/</code> →
          <b>http://localhost:5173</b>.</li>
        <li><a style="color:#00d18f" href="/docs">/docs</a> — explorador interactivo</li>
        <li><a style="color:#00d18f" href="/api/health">/api/health</a> — health check</li>
      </ul>
      </body></html>""")


def _check_local_caller(request: Request):
    """Solo UI propia y extensión sideload. Sin esto, cualquier página
    web local podría POSTear a /auth/import (loopback es alcanzable).
    Sin Origin (curl/scripts): loopback ya es el perímetro, pasa."""
    origin = (request.headers.get("origin") or "").strip()
    if not origin:
        return
    if origin.startswith(("chrome-extension://", "tauri://",
                          "https://tauri.localhost")):
        return
    try:
        from urllib.parse import urlparse
        u = urlparse(origin)
    except Exception:
        err("origen ilegible", 403)
    if u.scheme in ("http", "https") and u.hostname in ("localhost",
                                                         "127.0.0.1",
                                                         "::1"):
        return
    err(f"origen no permitido: {origin}"[:120], 403)


# ---------- read (seguras) ----------

@app.get("/api/health")
def health():
    from core.fetch import get_counts
    return {"ok": True, "sleeper_calls": get_counts(),
            "frontend": os.path.isfile(_DIST_INDEX),
            "version": APP_VERSION,
            # El shell compara esto contra su propia versión: si no
            # cuadran, mata al backend viejo y levanta el suyo. Sin el
            # pid no puede (ver backend_matching en src-tauri/src/main.rs).
            "pid": os.getpid(),
            # Observabilidad de los atajos que NO cambiamos (ver planes):
            # si son ~0, el ítem se muere solo sin tocar frescura.
            "observacion": {**pub.cache_stats(), **priv.pending_stats()}}


def _league_row(x, _bundle=None):
    """Una liga verificada contra Sleeper. fetched_at = dato real as of.
    Con bundle: 2 llamadas (league + rosters) en vez de 5.

    Solo para pintar: usa el bundle cacheado de display. Los writers y los
    availability checks usan `get_league_bundle` en vivo, sin cache."""
    lid = x["league_id"]
    try:
        b = _bundle or pub.get_bundle_display(lid, _uid())
        lgfull = b["league"] or {}
        roster = b["roster"] or {}
        wm = pub.waiver_mode(lid, _league=lgfull)
        # Claim (waiver_type 0/1) no tiene FAAB: el waiver_budget que
        # devuelve Sleeper (ej. 100) es un default sin sentido. n/a.
        left = (None if wm["mode"] == "claim"
                else pub.remaining_budget(lid, _uid(), _league=lgfull, _roster=roster))
        rs = (roster.get("settings") or {})
        record = f"{rs.get('wins', 0)}-{rs.get('losses', 0)}"
        lg = (lgfull.get("settings", {}) or {})
        resolve = {"waiver_day_of_week": lg.get("waiver_day_of_week"),
                   "waiver_clear_days": lg.get("waiver_clear_days"),
                   "daily_waivers": lg.get("daily_waivers"),
                   "leg": lg.get("leg", 1)}
    except Exception:
        wm, left, roster, record, resolve, lg, lgfull = (
            {"mode": "?", "waiver_type": -1, "bid_min": 0}, None, {}, "?", {}, {}, {})
    return {"league_id": lid, "name": x.get("name"),
            "roster_positions": (lgfull.get("roster_positions") or []),
            "mode": wm["mode"], "waiver_type": wm["waiver_type"],
            "budget_left": left, "bid_min": wm.get("bid_min"),
            "roster_id": roster.get("roster_id"),
            "waiver_priority": (roster.get("settings") or {}).get("waiver_position"),
            "record": record,
            "players_count": len(roster.get("players") or []),
            "resolve": resolve,
            "fetched_at": int(time.time() * 1000),
            "roster_space": pub.roster_space(lgfull, roster)}


def _season_default():
    """Temporada NFL vigente, si el usuario no puso SEASON en .env.

    La temporada N no termina en enero: sigue hasta abril, con el draft.
    Es abril-dentro y el fantasy sigue siendo 2026, y por ahi es cuando se
    resetea el FAAB. Recien en mayo empieza la 2027. Ese "hasta abril" es
    el dato: con un `if month < 3` (que es lo que se escribe por reflejo)
    en enero-marzo se apunta a la temporada equivocada y `get_leagues`
    devuelve [] -> el import cae en "no devuelve ligas" y culpa a la
    cuenta del usuario en vez de a la constante.

    Importa sobre todo en dynasty; en redraft la siguiente liga ya nace
    con el anio nuevo. `.env` manda siempre si hay SEASON ahi."""
    import datetime
    now = datetime.datetime.now()
    return str(now.year if now.month >= 5 else now.year - 1)


def _season():
    """SEASON de .env, o la vigente."""
    return str(os.environ.get("SEASON") or _season_default())


def _purge_account_caches():
    """Borra TODO lo cacheado de la cuenta anterior.

    El cache es display y local, pero ninguna de esas tablas lleva el
    user id: `leagues_cache`, `rosters_cache` y `league_snapshot` se
    clonaron por `league_id`, y `league_snapshot` ademas no se podaba (solo
    INSERT OR REPLACE, nunca borraba las que salian de la lista). Asi que
    cambiar de cuenta Sleeper en el mismo data dir dejaba la lista del
    primero servida al segundo cuando la lectura publica fallaba, con
    `stale: true` y todo. Datos que cruzan de una cuenta a otra: es el
    mismo tipo de fuga que se cerro en los mensajes de error, pero por el
    camino del fallback local.

    Los claims, la watchlist y el wire NO se tocan: son de la persona que
    esta usando la maquina, no de la cuenta de Sleeper."""
    try:
        cx = connect()
        for tabla in ("league_snapshot", "leagues_cache", "rosters_cache"):
            cx.execute(f"DELETE FROM {tabla}")
        cx.commit()
        cx.close()
    except Exception as e:
        # Purgar es defensa en profundidad: si falla, el snapshot igual
        # queda scoped por uid (ver _league_snapshot_read).
        print(f"[auth] no pude purgar el cache: {e!r}", file=sys.stderr)
    _CLEARS_CACHE.clear()
    try:
        pub.clear_process_caches()
    except Exception as e:
        print(f"[auth] no pude limpiar el cache en memoria: {e!r}",
              file=sys.stderr)


def _league_snapshot_write(rows, uid):
    """Guarda la última lista buena en SQLite. Es el plan B para cuando
    Sleeper no responde (solo lectura: nunca se finge que un writer corrió).

    Scopada por cuenta: `uid` va en su propia columna y la lectura filtra
    por el. Y ahora PODA: las ligas que ya no estan en la lista se borran,
    porque si no un snapshot de hace semanas revivía una liga de la que
    te sacaron cada vez que Sleeper caía."""
    try:
        cx = connect()
        cx.execute("CREATE TABLE IF NOT EXISTS league_snapshot ("
                   "league_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                   "synced_at INTEGER NOT NULL)")
        cols = [r[1] for r in cx.execute("PRAGMA table_info(league_snapshot)")]
        if "uid" not in cols:
            cx.execute("ALTER TABLE league_snapshot ADD COLUMN uid TEXT")
        now = int(time.time() * 1000)
        keep = []
        for r in rows:
            cx.execute("INSERT OR REPLACE INTO league_snapshot "
                       "(league_id, payload, synced_at, uid) VALUES (?,?,?,?)",
                       (r["league_id"], _json.dumps(r), now, str(uid or "")))
            keep.append(r["league_id"])
        if keep:
            cx.execute("DELETE FROM league_snapshot WHERE league_id NOT IN "
                       "(" + ",".join("?" * len(keep)) + ")", keep)
        else:
            cx.execute("DELETE FROM league_snapshot")
        cx.commit()
        cx.close()
    except Exception as e:
        # El snapshot es una comodidad, nunca un requisito: si falla, la app
        # sigue normal (solo pierde el fallback).
        print(f"[leagues] no pude guardar el snapshot: {e!r}", file=sys.stderr)


def _league_snapshot_read(uid):
    """Snapshot del snapshot SOLO de esta cuenta.

    Filma por uid. Las filas viejas (sin uid) no matchean y quedan como
    no estar: una instalacion previa pierde su fallback offline, que es la
    direccion segura, en vez de servir el snapshot de otro."""
    try:
        cx = connect()
        got = cx.execute("SELECT payload FROM league_snapshot WHERE uid = ?",
                         (str(uid or ""),)).fetchall()
        cx.close()
        return [_json.loads(r["payload"]) for r in got]
    except Exception:
        return []


@app.get("/api/leagues")
def leagues():
    """Lista de ligas verificada contra Sleeper.

    Si Sleeper no responde, se sirve el último snapshot local (con
    `stale: true`) en vez de un error: ver tus ligas es lo mínimo que la app
    debe poder hacer sin red. La UI marca las de caché para que nadie
    decida con un dato viejo sin saberlo."""
    season = _season()
    uid = os.environ.get("SLEEPER_USER_ID", "")  # dinámico: el wizard
    if not uid:  # virgen/conectar pendiente: lista vacía, jamás 500
        return {"leagues": [], "stale": False}
    try:
        all_leagues = [x for x in pub.get_leagues(uid, season)
                       if (x.get("sport") or "nfl") == "nfl"]
    except Exception as e:
        snap = _league_snapshot_read(uid)
        if snap:
            print(f"[leagues] Sleeper no respondió, sirviendo snapshot "
                  f"de {len(snap)} ligas: {e!r}", file=sys.stderr)
            return {"leagues": snap, "stale": True}
        fail(E_SLEEPER_READ, "No pude leer tus ligas.", e)
    # Paralelo por liga: mismos calls que antes, ~4x más rápido en cold start.
    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(_league_row, all_leagues))
    _league_snapshot_write(rows, uid)
    return {"leagues": rows, "stale": False}


@app.get("/api/leagues/{league_id}")
def league_one(league_id: str):
    """Refresh de UNA liga. Directo al bundle (sin barrer la lista de
    ligas del usuario primero)."""
    try:
        b = pub.get_league_bundle(league_id, _uid())
    except Exception:
        err("liga no existe", 404)
    if not (b.get("league") or {}).get("league_id"):
        err("liga no existe", 404)
    return _league_row({"league_id": league_id,
                        "name": (b["league"] or {}).get("name")}, _bundle=b)


@app.get("/api/leagues/{league_id}/roster")
def roster(league_id: str):
    r = pub.get_my_roster(league_id, _uid())
    if not r:
        err("roster no encontrado", 404)
    pmap = pub.get_players_map()
    players = [{"player_id": pid,
                "name": pmap.get(str(pid), {}).get("name", pid),
                "pos": pmap.get(str(pid), {}).get("pos"),
                "team": pmap.get(str(pid), {}).get("team")}
               for pid in (r.get("players") or [])]
    # Orden de roster: QB RB WR TE K DEF; desconocidos al fondo por nombre.
    order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "K": 4, "DEF": 5}
    players.sort(key=lambda p: (order.get((p.get("pos") or "").upper(), 99),
                                p.get("name") or ""))
    # Taxi/IR viven dentro de players[] pero NO son dropeables via claim
    # (Sleeper muerde con error): se exponen para filtrar en UI + validador.
    return {"roster_id": r["roster_id"], "players": players,
            "taxi": [str(p) for p in (r.get("taxi") or [])],
            "reserve": [str(p) for p in (r.get("reserve") or [])]}


@app.get("/api/availability/{league_id}/{player_id}")
def availability(league_id: str, player_id: str):
    """¿El add tiene dueño en esta liga? Gate previo del modal (el backend
    lo revalida al enviar). Solo lectura publica."""
    try:
        taken = str(player_id) in pub.get_rostered_ids(league_id)
    except Exception as e:
        fail(E_SLEEPER_READ, "No pude comprobar si ese jugador ya está en algún roster.", e)
    return {"taken": taken}


class AvailMatrix(BaseModel):
    league_ids: list[str] = []
    player_ids: list[str] = []


@app.post("/api/availability/matrix")
def availability_matrix(body: AvailMatrix):
    """Taken por liga para un set de players: rostered ∪ claims abiertos
    propios (ready/submitted: tu waiver pendiente también bloquea el +).
    Una lectura publica por liga; la liga que falle se omite en taken
    (el modal/backend gatea igual)."""
    want = set(str(p) for p in body.player_ids)
    cx = connect()
    open_rows = cx.execute(
        "SELECT league_id, player_id FROM claims"
        " WHERE status IN ('ready','submitted')").fetchall()
    pending: dict = {}
    for r in open_rows:
        if str(r["player_id"]) in want:
            pending.setdefault(r["league_id"], set()).add(str(r["player_id"]))
    out: dict = {}
    pend: dict = {}
    for lid in body.league_ids:
        try:
            owned = pub.get_rostered_ids(lid) & want
        except Exception:
            continue
        mine = pending.get(lid, set())
        out[lid] = sorted(owned | mine)
        if mine:
            pend[lid] = sorted(mine)
    return {"taken": out, "pending": pend}


@app.get("/api/debug/transactions/{league_id}",
              dependencies=[Depends(_check_local_caller)])
def debug_transactions(league_id: str, week: int = 0):
    """Transactions públicas RAW de la liga/semana para descubrir qué
    metadata escupe Sleeper (winner, reasons de failed). Solo lectura.
    Waivers completos en crudo + conteo del resto (la semana entera pesa)."""
    wk = int(week or current_week_default())
    try:
        txs = pub.get_transactions(league_id, wk)
    except Exception as e:
        fail(E_SLEEPER_READ, "No pude leer los movimientos de la liga.", e)
    waivers = [t for t in txs if t.get("type") == "waiver"]
    counts: dict = {}
    for t in txs:
        k = f"{t.get('type')}/{t.get('status')}"
        counts[k] = counts.get(k, 0) + 1
    return {"league_id": league_id, "week": wk, "counts": counts,
            "waivers": waivers}


@app.get("/api/debug/player-settings/{league_id}/{player_id}",
              dependencies=[Depends(_check_local_caller)])
def debug_player_settings(league_id: str, player_id: str):
    """Fila RAW de league_players (settings+metadata sin parsear) para
    derivar el discriminador FA/WAIVER. Solo lectura. null = ausente
    (la ausencia también es señal)."""
    try:
        row = priv.get_player_row(league_id, player_id)
    except Exception as e:
        fail(E_SLEEPER_READ, "No pude leer los ajustes del jugador.", e)
    return {"player_id": str(player_id), "row": row}


@app.get("/api/pending/{league_id}")
def pending(league_id: str, fresh: int = 0):
    rid = pub.find_roster_id(league_id, _uid())
    try:
        # Cache 30s: colapsa con el verify(lid) que Hub pide seguido.
        # fresh=1 (↻ por liga): bypass — orden live garantizado.
        rows = priv.list_pending_cached(league_id, rid,
                                        fresh=bool(fresh))
    except Exception as e:
        fail(E_SLEEPER_READ, "No pude leer tus claims pendientes.", e)
    return _sleeper_order(rows)


def _sleeper_order(rows):
    """Espeja el orden de la UI de Sleeper: los pendings traen
    settings.priority (0 = arriba, caso Penix/Wilson) pero el API los
    devuelve por created desc. Sin priority en todas las filas (FAAB:
    solo waiver_bid) se respeta el orden del API."""
    def key(t):
        s = (t.get("settings") or {})
        p = s.get("priority")
        if isinstance(p, bool) or not isinstance(p, (int, float)):
            return (1, 0, t.get("created") or 0)
        return (0, int(p), t.get("created") or 0)
    if rows and all(key(t)[0] == 0 for t in rows):
        return sorted(rows, key=key)
    return rows


@app.get("/api/debug/pending-order/{league_id}",
             dependencies=[Depends(_check_local_caller)])
def debug_pending_order(league_id: str):
    """Compara el orden del query angosto (waiver+pending) vs el amplio
    (toda la historia, filtrado en Python). Diagnóstico: si difieren,
    el filtro server-side reordena y hay que espejar el amplio.
    Solo lectura. Devuelve tids en orden + created para ver la clave."""
    rid = pub.find_roster_id(league_id, _uid())
    try:
        narrow = [t for t in priv.list_transactions(league_id, rid, narrow=True)
                  if t.get("type") == "waiver" and t.get("status") == "pending"]
        wide = [t for t in priv.list_transactions(league_id, rid, narrow=False)
                if t.get("type") == "waiver" and t.get("status") == "pending"]
    except Exception as e:
        fail(E_SLEEPER_READ, "No pude leer el orden de tus claims.", e)

    def slim(t):
        adds = t.get("adds") or {}
        return {"tid": str(t.get("transaction_id")),
                "adds": sorted(str(a) for a in adds),
                "created": t.get("created"),
                "status_updated": t.get("status_updated"),
                "settings": t.get("settings"),
                "metadata": t.get("metadata")}
    return {"narrow": [slim(t) for t in narrow],
            "wide": [slim(t) for t in wide],
            "same_order": [t.get("transaction_id") for t in narrow]
            == [t.get("transaction_id") for t in wide]}


@app.get("/api/search")
def search(q: str, limit: int = 10):
    limit = max(1, min(int(limit), 25))  # capeado para pruebas
    return [{"player_id": pid, **v}
            for pid, v in pub.search_players(q, limit)]


def _fantasy_positions(roster_positions):
    """Posiciones fantasy SEGÚN la liga. No es un set fijo: una liga sin
    K no debe listar kickers y una sin DEF/DST no debe listar defensas
    (en el ejemplo, solo NUCLEAR tiene ambos).
    Sleeper nombra el slot de defensa `DEF` (algunos `DST`); el
    `position` del jugador también es `DEF`. FLEX no genera filtro
    propio (sus jugadores ya tienen QB/RB/WR/TE).
    Orden estable para las tabs: QB, RB, WR, TE, K, DEF."""
    if not roster_positions:
        return ["QB", "RB", "WR", "TE", "K", "DEF"]
    out = []
    for slots, player_pos in ((("QB",), "QB"), (("RB",), "RB"),
                              (("WR",), "WR"), (("TE",), "TE"),
                              (("K",), "K"), (("DEF", "DST"), "DEF")):
        if any(s in roster_positions for s in slots) and player_pos not in out:
            out.append(player_pos)
    return out or ["QB", "RB", "WR", "TE", "K", "DEF"]


@app.get("/api/free-agents/{league_id}")
def free_agents(league_id: str, q: str = "", limit: int = 20,
                pos: str = "", sort: str = "proj", week: int = 0):
    """Agentes libres = mapa completo menos rostered. Default: proj semanal
    desc (mirror UI), fallback a search_rank. Default 20, tope 100."""
    limit = max(1, min(int(limit), 100))
    taken = pub.get_rostered_ids(league_id)
    pmap = pub.get_players_map()
    # Clears completo (incl. vencidos) para el pool: cualquier rastro de
    # waiver-relevancia amerita proyección. Los badges usan solo futuro.
    try:
        clears_all = waiver_clears_cached(league_id, keep_past=True)["data"]
    except Exception:
        clears_all = {}
    _now_ms = int(time.time() * 1000)
    clears = {pid: ms for pid, ms in clears_all.items() if ms > _now_ms}
    ql = (q or "").lower()
    # Posiciones que la LIGA rosteriza (regla de la liga, no un set fijo):
    # una liga sin DST no debe listar defensas, una sin K no debe listar
    # kickers. Solo fantasy: fuera DB/LB/DL/etc (DTO lazy de Sleeper).
    try:
        lg_full = pub.get_league_full(league_id)
    except Exception:
        lg_full = {}
    rpos = [str(p).upper() for p in (lg_full.get("roster_positions") or [])]
    FANTASY_POS = _fantasy_positions(rpos)
    out = []
    for pid, v in pmap.items():
        if pid in taken:
            continue
        if not v.get("team"):  # sin equipo = irrelevante para FA
            continue
        if (v.get("pos") or "").upper() not in FANTASY_POS:
            continue
        if pos and (v.get("pos") or "") != pos.upper():
            continue
        if ql and ql not in (v.get("name") or "").lower():
            continue
        out.append({"player_id": pid, "clears_at": clears.get(pid), **v})
    if sort == "rank":
        out.sort(key=_rankkey)
        return out[:limit]
    # Default proj: rank para ordenar candidatos + dropeados recientes
    # (clears: justo los breakouts que el rank entierra, ej. Raheim
    # rank 690). Las proyecciones vienen del REST público (el que usa la
    # propia UI): mapa completo local, sin JWT, una llamada con cache 6h.
    out.sort(key=_rankkey)
    wk = _leg_week(league_id, week)
    if clears_all:
        have = {d["player_id"] for d in out}
        for pid in clears_all:
            if pid in have or pid in taken:
                continue
            v = pmap.get(pid)
            if not v or not v.get("team"):
                continue
            if (v.get("pos") or "").upper() not in FANTASY_POS:
                continue
            if pos and (v.get("pos") or "") != pos.upper():
                continue
            if ql and ql not in (v.get("name") or "").lower():
                continue
            out.append({"player_id": pid, "clears_at": clears.get(pid), **v})
    season = _season()
    # Puntos = stats × scoring de LA LIGA (público). Sin esto, sortea por
    # `pts_ppr` que es scoring estándar y no coincide con la UI.
    try:
        lg_settings = pub.get_league_full(league_id).get("settings") or {}
    except Exception as e:
        fail(E_SLEEPER_READ, "No pude leer los ajustes de la liga.", e)
    scoring_settings = pub.get_scoring_settings(league_id)
    # Ritmo de waivers: hay ligas diarias y semanales, con distinto
    #_clear_days_. Se expone como texto (sin inventar fecha).
    w_daily = int(lg_settings.get("daily_waivers") or 0)
    w_clear = int(lg_settings.get("waiver_clear_days") or 0)
    cadence = ("waivers todos los días; resuelve al día siguiente" if w_clear else
               "waivers todos los días; resuelve el mismo día") if w_daily else (
        "esta liga corre waivers una vez por semana; resuelven "
        f"{w_clear} días después")
    try:
        pmap_proj = pub.get_projections_week(season, wk)
    except Exception as e:
        fail(E_PROY, f"No hay proyecciones de la semana {wk}.", e)
    if not pmap_proj:
        err(f"sin proyecciones sem {wk}: Sleeper devolvió vacío", 502)
    for d in out:
        p = pmap_proj.get(d["player_id"]) or {}
        d["opp"] = p.get("opp")
        d["proj"] = _proj_points(p.get("stats"), scoring_settings)
        # Cuándo resuelve: si está en waivers, su clears_at ES la fecha
        # que dio Sleeper (autoritativa). Si no, no hay fecha por jugador
        # en los datos de Sleeper: no se inventa.
        d["resolves_at"] = d.get("clears_at")
        d["cadence"] = cadence
        st = p.get("stats") or {}
        # Columnas de la tabla de Sleeper (ATT, RUSH YD/TD, REC REC/YD/TD,
        # CMP). Van como dato crudo proyectado de la semana.
        d["st"] = {
            "att": _round(st.get("pass_att")),
            "cmp": _round(st.get("pass_cmp")),
            "ryd": _round(st.get("rush_yd")),
            "rtd": _round(st.get("rush_td")),
            "rec": _round(st.get("rec")),
            "reyd": _round(st.get("rec_yd")),
            "reydtd": _round(st.get("rec_td")),
        }
    out.sort(key=_projkey)
    return out[:limit]


def _round(v):
    """Redondeo de UI para stats proyectados (enteros arriba, 1 decimal
    abajo). None si no hay dato."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    return int(round(v)) if v >= 10 else round(round(float(v), 1), 1)


def _proj_points(stats, scoring_settings):
    """Puntos proyectados de la semana = stats × scoring de la liga."""
    from core import scoring
    return scoring.points(stats or {}, scoring_settings or None)


def _leg_week(league_id: str, week: int) -> int:
    """Semana para proyecciones: explícita gana; si no, el leg vivo de
    la liga (Sleeper ya habla su propia numeración: sin +1 aquí).
    current_week_default es placeholder (semana 2 fija): último recurso."""
    if int(week or 0):
        return int(week)
    try:
        return int(resolve_leg(league_id) or 0) or current_week_default()
    except Exception:
        return current_week_default()


def _rankkey(d):
    # Orden Sleeper: search_rank asc; sin rank al fondo; nombre desempata.
    r = d.get("rank")
    return ((0, r, d.get("name") or "") if isinstance(r, int)
            else (1, 0, d.get("name") or ""))


def _projkey(d):
    # Con proyección primero (desc), resto por rank.
    p = d.get("proj")
    if isinstance(p, (int, float)):
        return (0, -p, d.get("name") or "")
    return (1, *_rankkey(d)[1:])


# ---------- waiver clears exactos (mirror UI: league_players.settings) ----------

_CLEARS_CACHE: dict = {}  # league_id -> (epoch_s, {player_id: clears_at_ms})
CLEARS_TTL = 900  # 15 min; el botón ↻ del header revienta con ?fresh=1


def waiver_clears_cached(league_id: str, fresh: bool = False,
                         keep_past: bool = False) -> dict:
    """Mirror league_players. El cache guarda TODO (incl. vencidos);
    por default se devuelven solo futuros (badge = lock real).
    keep_past=True para distinguir 'libre seguro' (ts pasado) de
    'ausente del mapa' (recién dropeado o fuera del subset: incierto,
    jamás bloquear).

    Si Sleeper falla pero ya teníamos un map para esa liga, se devuelve el
    viejo en vez de reventar: el badge se puede quedar unos minutos viejo,
    pero la app sigue siendo usable. Quien llama marca `stale` para que la
    UI lo diga."""
    now = time.time()
    hit = _CLEARS_CACHE.get(league_id)
    age_s = None
    if hit and not fresh and now - hit[0] < CLEARS_TTL:
        data = hit[1]
        stale = False
    else:
        try:
            data = priv.get_waiver_clears(league_id)
            _CLEARS_CACHE[league_id] = (now, data)
            stale = False
        except Exception as e:
            if hit:
                # Fallback local: preferimos un dato viejo marcado a un
                # error. `age_s` dice qué tan viejo es.
                print(f"[clears] {league_id}: fallo de Sleeper, "
                      f"serviendo cache de {int(now - hit[0])}s: {e!r}",
                      file=sys.stderr)
                data, stale = hit[1], True
                age_s = int(now - hit[0])
            else:
                raise
    out = {"stale": stale, "age_s": age_s}
    if keep_past:
        out["data"] = data
    else:
        now_ms = int(now * 1000)
        out["data"] = {pid: ms for pid, ms in data.items() if ms > now_ms}
    return out


@app.get("/api/waiver-clears/{league_id}")
def waiver_clears(league_id: str, fresh: int = 0, past: int = 0):
    """Clears exactos de Sleeper. Cache 15min salvo fresh=1. past=1
    incluye vencidos (distinguir libre-seguro de ausente-incierto).

    Respuesta: {clears: {player_id: ms}, stale: bool}. `stale` =true
    significa que Sleeper no respondió y esto es lo último que se leyó;
    la UI lo muestra para que nobody decida un claim sobre una fecha vieja
    sin saberlo."""
    try:
        r = waiver_clears_cached(league_id, fresh=bool(fresh),
                                 keep_past=bool(past))
    except Exception as e:
        fail(E_SLEEPER_READ, "No pude leer los clears de la liga.", e)
    return {"clears": r["data"], "stale": r["stale"], "age_s": r["age_s"]}


# ---------- freshness (Tier-0, 100% local, cero llamadas a Sleeper) ----------

STALE_H = 12  # submitted sin verificar por mas de esto -> banner
JWT_WARN_H = 72  # avisar si el JWT vence antes de esto


def _jwt_status(tok=None):
    import base64 as _b64
    import json as _json
    if tok is None:
        tok = os.environ.get("SLEEPER_JWT", "")
    if not tok:
        return {"status": "missing", "expires_at": None}
    try:
        seg = tok.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        exp = _json.loads(_b64.urlsafe_b64decode(seg)).get("exp")
        if not exp:
            return {"status": "unknown", "expires_at": None}
        ms = int(exp) * (1000 if int(exp) < 1_000_000_000_000 else 1)
        left_h = (ms - int(time.time() * 1000)) / 3600000
        if left_h <= 0:
            st = "expired"
        elif left_h < JWT_WARN_H:
            st = "expiring"
        else:
            st = "valid"
        return {"status": st, "expires_at": ms,
                "hours_left": round(left_h, 1)}
    except Exception:
        return {"status": "invalid", "expires_at": None}


@app.get("/api/freshness")
def freshness():
    """Auditoria local de arranque. NO toca Sleeper (sin pub/priv aqui)."""
    from core.db import DEFAULT_PATH
    cx = connect()
    # Staleness mira checked_at primero: verify() lo estampa, y solo asi
    # el banner se apaga. sent_at es fallback (filas jamás verificadas).
    sub = cx.execute("SELECT COUNT(*), MIN(COALESCE(checked_at, sent_at))"
                     " FROM claims WHERE status='submitted'").fetchone()
    ready = cx.execute("SELECT COUNT(*) FROM claims"
                       " WHERE status='ready'").fetchone()[0]
    n_sub, oldest = int(sub[0]), sub[1]
    now = int(time.time() * 1000)
    age_h = (now - int(oldest)) / 3600000 if oldest else 999.0
    try:
        mtime = int(os.path.getmtime(DEFAULT_PATH))
    except OSError:
        mtime = None
    return {
        "jwt": _jwt_status(),
        "claims": {"submitted": n_sub, "ready": int(ready),
                   "oldest_unverified": int(oldest) if oldest else None,
                   "stale": bool(n_sub and age_h > STALE_H)},
        "clears_cache": {lid: round(now / 1000 - ts, 1)
                         for lid, (ts, _) in _CLEARS_CACHE.items()},
        "db_mtime": mtime,
    }


# ---------- auth asistido (wizard Conectar Sleeper, sin F12) ----------

_ENV_PATH = _env_path()


class AuthSave(BaseModel):
    jwt: str = ""
    session: str = ""
    user_id: str = ""  # opcional: si vacío se intenta sacar del JWT (sub)


def _jwt_claims(tok):
    """Payload del JWT como dict. {} si no decodifica. Sin verificar
    firma: solo lectura de claims para diagnóstico y user id."""
    import base64 as _b64
    import json as _json
    try:
        seg = (tok or "").split(".")[1]
        seg += "=" * (-len(seg) % 4)
        data = _json.loads(_b64.urlsafe_b64decode(seg))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _resolve_uid(jwt, explicit=""):
    """user id determinista, sin LLM ni scraping:
    1) campo manual, 2) claim numérico (sub/user_id/uid),
    3) claim username -> lookup público /v1/user/<username>.
    Retorna (uid, claim_names) para diagnóstico sin exponer valores."""
    if (explicit or "").strip().isdigit():
        return explicit.strip(), []
    claims = _jwt_claims(jwt)
    names = sorted(str(k) for k in claims.keys())
    for key in ("sub", "user_id", "uid", "userId"):
        val = str(claims.get(key) or "")
        if val.isdigit():
            return val, names
    uname = str(claims.get("username") or claims.get("preferred_username")
                or "").strip()
    if uname:
        try:
            uid = str((pub.get_user(uname) or {}).get("user_id") or "")
            if uid.isdigit():
                return uid, names
        except Exception:
            pass
    return "", names


def _write_env(want):
    """Escribe pares en .env preservando el resto. Sin logs de valores."""
    try:
        try:
            with open(_ENV_PATH, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            lines = []
        seen = set()
        out = []
        for ln in lines:
            key = ln.split("=", 1)[0].strip()
            if key in want:
                out.append(f"{key}={want[key]}")
                seen.add(key)
            else:
                out.append(ln)
        for key, val in want.items():
            if key not in seen:
                out.append(f"{key}={val}")
        with open(_ENV_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
    except OSError as e:
        fail(E_LOCAL, "No pude guardar tu sesión en disco.", e, 500)
    for key, val in want.items():
        os.environ[key] = val


@app.get("/api/auth/status")
def auth_status():
    """Estado del JWT sin tocar Sleeper (misma lectura que freshness)."""
    return {"jwt": _jwt_status()}


@app.post("/api/auth/save")
def auth_save(body: AuthSave):
    """Guarda JWT+sesión pegados del bookmarklet en .env (server-side).
    Valida expiración ANTES de guardar; jamás loguea valores.
    Actualiza os.environ en el proceso vivo: sin reinicio."""
    jwt = (body.jwt or "").strip()
    session = (body.session or "").strip()
    if not jwt:
        err("jwt vacío", 422)
    st = _jwt_status(jwt)
    if st["status"] in ("expired", "invalid"):
        err(f"jwt {st['status']}: farmea uno fresco en sleeper.com", 422)
    uid, names = _resolve_uid(jwt, body.user_id)
    if not uid:
        seen = ",".join(names) if names else "ilegible"
        err(f"guardado pero sin user id (claims: {seen}): pega el campo 3 o re-loguea", 422)
    want = {"SLEEPER_JWT": jwt, "SLEEPER_SESSION": session}
    if uid:
        want["SLEEPER_USER_ID"] = uid
    # Mismo guardia que /auth/import: antes de que _write_env pise el uid.
    prev = os.environ.get("SLEEPER_USER_ID", "")
    if prev and uid and prev != str(uid):
        _purge_account_caches()
    _write_env(want)
    return {"ok": True, "jwt": _jwt_status()}


@app.post("/api/auth/import")
def auth_import(body: AuthSave, request: Request):
    """Como /auth/save, pero con prueba viva contra Sleeper ANTES de
    guardar: una liga pública + un pending privado angosto con el token
    candidato. La usa la extensión. Un read público + uno privado, solo
    al importar; jamás escribe en Sleeper."""
    _check_local_caller(request)
    jwt = (body.jwt or "").strip()
    session = (body.session or "").strip()
    if not jwt:
        err("jwt vacío", 422)
    st = _jwt_status(jwt)
    if st["status"] in ("expired", "invalid"):
        err(f"jwt {st['status']}: farmea uno fresco en sleeper.com", 422)
    uid, names = _resolve_uid(jwt, body.user_id)
    if not uid:
        seen = ",".join(names) if names else "ilegible"
        err(f"sin user id (claims: {seen}): pégalo manual (campo 3 del modal)", 422)
    n = _prove_and_save(jwt, session, uid)
    return {"ok": True, "jwt": _jwt_status(), "leagues": n}



def _prove_and_save(jwt, session, uid):
    """Prueba viva (1 read público + 1 privado) y guardado en .env +
    environ. La usa /auth/import. Retorna #ligas."""
    try:
        season = _season()
        leagues = [x for x in pub.get_leagues(uid, season)
                   if (x.get("sport") or "nfl") == "nfl"]
    except Exception as e:
        fail(E_TOKEN, "El token funcionó pero no pude leer tus ligas.", e)
    if not leagues:
        # La cuenta puede estar bien y la temporada apuntando a la que no
        # es: en `/leagues/nfl/{season}` una temporada equivocada devuelve []
        # y no un error, así que sin esto el mensaje culpa a la cuenta.
        err(f"No encontré ligas NFL en la temporada {season}. Si estás en "
            f"offseason o tu liga es de otro año, pon SEASON=<año> en .env.",
            422)
    lid = leagues[0]["league_id"]
    try:
        rid = pub.find_roster_id(lid, uid)
        priv.list_transactions(lid, rid, jwt=jwt, session=session,
                               timeout=20, narrow=True)
    except Exception as e:
        fail(E_TOKEN, "Sleeper no aceptó ese token. Vuelve a iniciar sesión en sleeper.com.", e)
    # Antes de _write_env: ahí os.environ ya quedó con el uid nuevo y la
    # comparación "cambió la cuenta?" nunca dispararía.
    prev = os.environ.get("SLEEPER_USER_ID", "")
    if prev and prev != str(uid):
        _purge_account_caches()
    _write_env({"SLEEPER_JWT": jwt, "SLEEPER_SESSION": session,
                "SLEEPER_USER_ID": uid})
    return len(leagues)


@app.post("/api/verify")
def verify(body: dict):
    """Tier-1: reconcilia submitted vs Sleeper (solo lectura remota;
    escribe solo DB local). Opcional {"league_id": ...} para scope.
    Pasa mi roster_id por liga para compartir el cache de pendings con
    /api/pending (memo: cero llamadas extra)."""
    from core.verify import run_verify
    lid = (body or {}).get("league_id")
    try:
        cx = connect()
        rows = cx.execute("SELECT DISTINCT league_id FROM claims"
                          " WHERE status='submitted'" + (" AND league_id=?" if lid else ""),
                          ([lid] if lid else [])).fetchall()
        rids = {r["league_id"]: pub.find_roster_id(r["league_id"], _uid())
                for r in rows}
        return run_verify(cx, league_id=lid, roster_ids=rids)
    except Exception as e:
        fail(E_SLEEPER_READ, "No pude verificar los claims enviados.", e)


@app.post("/api/verify/backfill-bids",
               dependencies=[Depends(_check_local_caller)])
def verify_backfill(body: dict):
    """One-shot hacia atrás: re-sincroniza league_bid en filas ya
    resolved desde el rastro público (Winston y compañía). Opcional
    {"league_id": ...}. Solo lectura remota; escribe bids locales."""
    from core.verify import run_backfill
    lid = (body or {}).get("league_id")
    try:
        return run_backfill(connect(), league_id=lid)
    except Exception as e:
        fail(E_SLEEPER_READ, "No pude recuperar las pujas de los claims.", e)


# ---------- watchlist + claims (DB local, sin tocar Sleeper) ----------

class WatchItem(BaseModel):
    player_id: str
    base_bid: int = 0
    notes: str = ""


@app.post("/api/watchlist")
def watch_add(item: WatchItem):
    cx = connect()
    mx = cx.execute("SELECT MAX(priority) FROM watchlist").fetchone()[0]
    cx.execute("INSERT OR REPLACE INTO watchlist"
               " (player_id, base_bid, priority, notes) VALUES (?,?,?,?)",
               (item.player_id, item.base_bid, (mx or 0) + 1, item.notes))
    cx.commit()
    return {"ok": True}


@app.get("/api/watchlist")
def watch_list():
    cx = connect()
    pmap = pub.get_players_map()
    out = []
    for r in cx.execute("SELECT * FROM watchlist ORDER BY priority"):
        d = dict(r)
        info = pmap.get(d["player_id"], {})
        d["name"] = info.get("name", d["player_id"])
        d["pos"] = info.get("pos")
        d["team"] = info.get("team")
        out.append(d)
    return out


@app.patch("/api/watchlist/reorder")
def watch_reorder(body: dict):
    cx = connect()
    for i, pid in enumerate(body.get("ordered_ids", [])):
        cx.execute("UPDATE watchlist SET priority=? WHERE player_id=?", (i, pid))
    cx.commit()
    return {"ok": True}


@app.delete("/api/watchlist/{player_id}")
def watch_del(player_id: str):
    cx = connect()
    cx.execute("DELETE FROM watchlist WHERE player_id=?", (player_id,))
    cx.commit()
    return {"ok": True}


class ApplyAll(BaseModel):
    week: int = 0
    drop_player_id: str = ""  # drop default; ajustable por claim


@app.post("/api/claims/apply-all",
               dependencies=[Depends(_check_local_caller)])
def claims_apply_all(body: ApplyAll):
    week = body.week or current_week_default()
    cx = connect()
    league_ids = [x["league_id"] for x in leagues()]
    watch = watch_list()
    n = 0
    for w in watch:
        for lid in league_ids:
            bid = w["base_bid"] if _mode(lid) == "faab" else 0
            cx.execute("INSERT INTO claims (player_id, league_id, week,"
                       " watchlist_priority, base_bid, league_bid,"
                       " drop_player_id, status, user_status)"
                       " VALUES (?,?,?,?,?,?,?,'draft','draft')",
                       (w["player_id"], lid, week, w["priority"],
                        w["base_bid"], bid, body.drop_player_id))
            n += 1
    cx.commit()
    return {"created": n, "week": week}


def _mode(league_id: str) -> str:
    return pub.waiver_mode(league_id)["mode"]


@app.get("/api/claims")
def claims_list(week: int = 0, status: str = "", league_id: str = ""):
    cx = connect()
    q = "SELECT * FROM claims WHERE 1=1"
    args: list = []
    if week:
        q += " AND week=?"; args.append(week)
    if status:
        q += " AND status=?"; args.append(status)
    if league_id:
        q += " AND league_id=?"; args.append(league_id)
    rows = [dict(r) for r in cx.execute(q + " ORDER BY id", args)]
    # Nombres para que el Enviar muestre qué dispara (solo cache local).
    pmap = pub.get_players_map()
    for d in rows:
        d["name"] = pmap.get(str(d["player_id"]), {}).get("name",
                                                          d["player_id"])
        dp = d.get("drop_player_id")
        d["drop_name"] = (pmap.get(str(dp), {}).get("name", dp) if dp else "")
    return rows


def _free_slots(lid, roster=None, _league=None):
    """Slots libres (fórmula única en pub.roster_space). Nunca levanta:
    sin datos = 0 (fail-closed). Con bundle: cero llamadas extra."""
    try:
        lg = _league if _league is not None else pub.get_league(lid)
        r = roster if roster is not None else pub.get_my_roster(lid, _uid())
        return int(pub.roster_space(lg, r or {}).get("free", 0))
    except Exception:
        return 0


def _locked_drops(roster):
    """Ocupantes de taxi/IR: no dropeables via claim."""
    r = roster or {}
    return (set(str(p) for p in (r.get("taxi") or []))
            | set(str(p) for p in (r.get("reserve") or [])))


@app.post("/api/claims/{cid}/ready")
def claim_ready(cid: int):
    cx = connect()
    r = cx.execute("SELECT * FROM claims WHERE id=?", (cid,)).fetchone()
    if not r:
        err("claim no existe", 404)
    lid = r["league_id"]
    b = pub.get_league_bundle(lid, _uid())
    wm = pub.waiver_mode(lid, _league=b["league"])
    roster = b["roster"]
    left = pub.remaining_budget(lid, _uid(), _league=b["league"], _roster=roster)
    c = Claim(player_id=r["player_id"], league_id=lid, week=r["week"],
              drop_player_id=r["drop_player_id"], league_bid=r["league_bid"],
              id=r["id"])
    valid, reason = validate_claim(
        c, mode=wm["mode"], roster_players=(roster or {}).get("players") or [],
        budget_left=left, bid_min=wm.get("bid_min", 0),
        rostered_all=pub.get_rostered_ids(lid, _rosters=b["rosters"]),
        free_slots=_free_slots(lid, roster, _league=b["league"]),
        locked_drops=_locked_drops(roster))
    if not valid:
        err(reason, 422)
    cx.execute("UPDATE claims SET status='ready', user_status='ready' WHERE id=?",
                (cid,))
    cx.commit()
    return {"ok": True}


class QuickClaim(BaseModel):
    league_id: str
    player_id: str
    drop_player_id: str = ""
    bid: int = 0
    week: int = 0


@app.post("/api/claims/quick")
def claim_quick(body: QuickClaim):
    """Crea + valida un claim suelto (hub). No toca Sleeper.
    Un bundle (2 calls) en vez de 6 lecturas sueltas."""
    week = body.week or current_week_default()
    lid = body.league_id
    b = pub.get_league_bundle(lid, _uid())
    wm = pub.waiver_mode(lid, _league=b["league"])
    roster = b["roster"]
    left = pub.remaining_budget(lid, _uid(), _league=b["league"], _roster=roster)
    bid = body.bid if wm["mode"] == "faab" else 0
    c = Claim(player_id=body.player_id, league_id=lid, week=week,
              drop_player_id=body.drop_player_id, league_bid=bid)
    valid, reason = validate_claim(
        c, mode=wm["mode"], roster_players=(roster or {}).get("players") or [],
        budget_left=left, bid_min=wm.get("bid_min", 0),
        rostered_all=pub.get_rostered_ids(lid, _rosters=b["rosters"]),
        free_slots=_free_slots(lid, roster, _league=b["league"]),
        locked_drops=_locked_drops(roster))
    if not valid:
        err(reason, 422)
    # Lock: NO pre-bloquear por clears_at. El campo se vuelve rancio
    # (rollover no lo reescribe: caso Raheim, ts pasado pero lockeado).
    # Único gate duro: rostered (completo y exacto). Sleeper valida el
    # lock al enviar y su error se superficie tal cual.
    cx = connect()
    cur = cx.execute("INSERT INTO claims (player_id, league_id, week,"
                     " league_bid, drop_player_id, status, user_status)"
                     " VALUES (?,?,?,?,?,'ready','ready')",
                     (c.player_id, lid, week, bid, c.drop_player_id))
    cx.commit()
    return {"ok": True, "id": cur.lastrowid}


class PickupBody(BaseModel):
    league_id: str
    player_id: str
    drop_player_id: str = ""
    week: int = 0


@app.post("/api/free-agents/pickup")
def free_agent_pickup(body: PickupBody):
    """Pickup directo de FA libre (mirror league_create_transaction).
    Completa AL INSTANTE: sin pending que cancelar. Writer: NO probar
    en vivo sin aprobacion."""
    import json as _json
    import time as _time
    lid = body.league_id
    # El leg vivo de la liga, no el placeholder: el HAR del add libre
    # mostró leg:3 y la fila del ledger debe decir eso.
    week = _leg_week(lid, body.week)
    b = pub.get_league_bundle(lid, _uid())
    roster = b["roster"]
    if not roster:
        err("roster no encontrado", 404)
    players = [str(p) for p in (roster.get("players") or [])]
    if str(body.player_id) in pub.get_rostered_ids(lid, _rosters=b["rosters"]):
        err("Add ya tiene dueño", 422)
    drop = str(body.drop_player_id or "")
    if drop:
        if drop not in players:
            err("Drop no esta en tu roster", 422)
        if drop == str(body.player_id):
            err("No puedes dropear al mismo que agregas", 422)
    elif _free_slots(lid, roster, _league=b["league"]) <= 0:
        err("Sin drop y sin slots libres", 422)
    rid = pub.find_roster_id(lid, _uid(), _rosters=b["rosters"])
    ok, data, raw = priv.add_free_agent(lid, body.player_id, drop, rid)
    now = int(_time.time() * 1000)
    cx = connect()
    cur = cx.execute(
        "INSERT INTO claims (player_id, league_id, week, league_bid,"
        " drop_player_id, status, user_status, payload_json, http_status,"
        " sent_at, transaction_id, error_text, resolved_at, resolution)"
        " VALUES (?,?,?,?,?,?,?, ?,?,?,?, ?,?,?)",
        (body.player_id, lid, week, 0, drop,
         "resolved" if ok else "failed", "ready",
         _json.dumps({"source": "pickup", "add": body.player_id,
                      "drop": drop}),
         200 if ok else None, now,
         (data or {}).get("transaction_id") if data else None,
         None if ok else raw[:500], now if ok else None,
         "won" if ok else None))
    cx.commit()
    if not ok:
        # `raw` (lo que respondió Sleeper) se queda en la DB para support y
        # en el campo `dev`; al usuario solo le llega la frase.
        fail(E_SLEEPER_WRITE, "Sleeper no aceptó el alta. Vuelve a intentarlo en un momento.", raw, dev=raw[:300])
    return {"ok": True, "id": cur.lastrowid,
            "transaction_id": (data or {}).get("transaction_id")}


@app.post("/api/claims/{cid}/send")
def claim_send(cid: int):
    """Envia UN claim (yolo). Writer: NO probar en vivo sin aprobacion."""
    ok, tid, http, msg = _send_one_claim(connect(), cid)
    if http == 404:
        err(msg, 404)
    if http == 409:
        err(msg, 409)
    if http == 422:
        err(msg, 422)
    return {"ok": ok, "transaction_id": tid}


class SendMany(BaseModel):
    ids: list[int] = []


@app.post("/api/claims/send-many")
def claims_send_many(body: SendMany):
    """Envía una lista cerrada de claims (multibid) secuencialmente con
    jitter. Solo los ids dados — nada de limbo ajeno. Writer: NO probar
    en vivo sin aprobacion."""
    import random as _random
    import time as _time
    cx = connect()
    out = []
    first = True
    for cid in body.ids:
        if not first:
            _time.sleep(3.5 + _random.random() * 2)
        first = False
        try:
            ok, tid, http, msg = _send_one_claim(cx, int(cid))
        except Exception as e:
            ok, tid, http, msg = False, None, None, str(e)
        # `msg` puede ser texto NUESTRO (404/409/422, ya en español y útil)
        # o el cuerpo crudo de Sleeper / una excepción. Lo segundo se
        # registra y sale como frase; el detalle va en `dev`.
        if not ok and http is None:
            print(f"[send-many] claim {cid} no envio: {msg!r}", file=sys.stderr)
            out.append({"id": int(cid), "ok": False, "transaction_id": None,
                        "error": "Sleeper no aceptó el envío. "
                                 "Vuelve a intentarlo en un momento.",
                        "code": E_SLEEPER_WRITE, "dev": (msg or "")[:300]})
        else:
            out.append({"id": int(cid), "ok": ok, "transaction_id": tid,
                        "error": None if ok else msg,
                        "code": None if ok else E_SLEEPER_WRITE})
    return {"results": out}


def _send_one_claim(cx, cid: int):
    """Núcleo del send (sin HTTP): revalida, envía, persiste.
    Retorna (ok, transaction_id|None, http|None, msg). http apunta el
    código que claim_send debe responder cuando no procede."""
    import json as _json
    import time as _time
    r = cx.execute("SELECT * FROM claims WHERE id=?", (cid,)).fetchone()
    if not r:
        return False, None, 404, "claim no existe"
    if r["status"] not in ("ready", "failed"):
        return False, None, 409, f"estado {r['status']} no enviable"
    # Revalidación pre-send (local + lecturas): el roster/presupuesto
    # pudo cambiar entre ready y send. Falla sin tocar Sleeper.
    # Un bundle (2 calls) en vez de 6 lecturas sueltas.
    _b = pub.get_league_bundle(r["league_id"], _uid())
    _wm = pub.waiver_mode(r["league_id"], _league=_b["league"])
    _roster = _b["roster"]
    _left = pub.remaining_budget(r["league_id"], _uid(),
                                 _league=_b["league"], _roster=_roster)
    _c = Claim(player_id=r["player_id"], league_id=r["league_id"],
               week=r["week"], drop_player_id=r["drop_player_id"],
               league_bid=r["league_bid"], id=r["id"])
    _valid, _reason = validate_claim(
        _c, mode=_wm["mode"], roster_players=(_roster or {}).get("players") or [],
        budget_left=_left, bid_min=_wm.get("bid_min", 0),
        rostered_all=pub.get_rostered_ids(r["league_id"], _rosters=_b["rosters"]),
        free_slots=_free_slots(r["league_id"], _roster, _league=_b["league"]),
        locked_drops=_locked_drops(_roster))
    if not _valid:
        cx.execute("UPDATE claims SET status='failed', error_text=?"
                   " WHERE id=?", (f"pre-send: {_reason}"[:500], cid))
        cx.commit()
        return False, None, 422, f"pre-send: {_reason}"
    taken = pub.get_rostered_ids(r["league_id"], _rosters=_b["rosters"])
    if str(r["player_id"]) in taken:
        cx.execute("UPDATE claims SET status='failed', error_text=?"
                   " WHERE id=?", ("Add ya tiene dueño (verificado pre-send)", cid))
        cx.commit()
        return False, None, 422, "Add ya tiene dueño (verificado pre-send)"
    # FAAB siempre lleva waiver_bid (incl. 0); claim nunca. El falsy-check
    # viejo (`if bid`) mandaba los $0 sin bid y Sleeper/UI los trataba
    # como claim. _wm ya se resolvió arriba.
    settings = (priv.build_faab_settings(r["league_bid"])
                if _wm["mode"] == "faab" else {})
    rid = pub.find_roster_id(r["league_id"], _uid(), _rosters=_b["rosters"])
    ok, data, raw = priv.submit_transaction(
        r["league_id"], r["player_id"], r["drop_player_id"], rid, settings)
    priv.bust_pending(r["league_id"])  # el pending cambió: reventar cache
    pub.bust_bundle_display(r["league_id"])  # roster/saldo: idem
    cx.execute("UPDATE claims SET status=?, payload_json=?, http_status=?,"
               " sent_at=?, transaction_id=?, error_text=? WHERE id=?",
               ("submitted" if ok else "failed",
                _json.dumps({"add": r["player_id"], "drop": r["drop_player_id"],
                             "bid": r["league_bid"]}),
                200 if ok else None, int(_time.time() * 1000),
                (data or {}).get("transaction_id") if data else None,
                None if ok else raw[:500], cid))
    cx.commit()
    return ok, (data or {}).get("transaction_id") if data else None, None, raw[:300] if not ok else ""


# ---------- writers: NO probar en vivo sin aprobacion ----------

# Ids en envío dentro de este proceso. Reemplaza al viejo estado
# 'submitting' (Fase 1C): la protección anti-doble-envío vive en memoria,
# nunca como estado visible. El sweep en core/db.py devuelve a ready
# cualquier 'submitting' huérfano de versiones viejas.
_SEND_INFLIGHT: set = set()


@app.post("/api/claims/submit")
def claims_submit(week: int = 0):
    """Procesa claims ready secuencialmente con jitter. SOLO manual."""
    import json as _json
    import random as _random
    import time as _time
    week = week or current_week_default()
    cx = connect()
    rows = [r for r in cx.execute(
        "SELECT * FROM claims WHERE status='ready' AND week=?", (week,))
        if r["id"] not in _SEND_INFLIGHT]
    _SEND_INFLIGHT.update(r["id"] for r in rows)
    try:
        modes: dict = {}  # league_id -> mode (un fetch publico por liga)
        rosters: dict = {}  # league_id -> rosters (un fetch por liga)
        for r in rows:
            # Puerta final: el roster pudo cambiar entre ready y send.
            # Solo lectura publica; si el add ya tiene dueño, se marca failed
            # sin tocar Sleeper.
            try:
                if r["league_id"] not in rosters:
                    rosters[r["league_id"]] = pub.get_rosters(r["league_id"])
                taken = pub.get_rostered_ids(r["league_id"],
                                             _rosters=rosters[r["league_id"]])
            except Exception as e:
                cx.execute("UPDATE claims SET status='failed', error_text=?"
                           " WHERE id=?", (f"no se pudo verificar roster: {e}"[:500], r["id"]))
                cx.commit()
                continue
            if str(r["player_id"]) in taken:
                cx.execute("UPDATE claims SET status='failed', error_text=?"
                           " WHERE id=?",
                           ("Add ya tiene dueño (verificado pre-send)", r["id"]))
                cx.commit()
                continue
            _time.sleep(3.5 + _random.random() * 2)
            try:
                # FAAB siempre lleva waiver_bid (incl. 0); claim nunca.
                # Fail-closed: sin modo no se envía (un $0 sin bid vuelve
                # como "claim" en UI y Sleeper lo malinterpreta).
                lid = r["league_id"]
                if lid not in modes:
                    modes[lid] = pub.waiver_mode(lid)["mode"]
                settings = (priv.build_faab_settings(r["league_bid"])
                            if modes[lid] == "faab" else {})
                rid = pub.find_roster_id(r["league_id"], _uid(),
                                           _rosters=rosters[r["league_id"]])
                ok, data, raw = priv.submit_transaction(
                    r["league_id"], r["player_id"], r["drop_player_id"], rid, settings)
                priv.bust_pending(r["league_id"])
                pub.bust_bundle_display(r["league_id"])
                cx.execute("UPDATE claims SET status=?, payload_json=?,"
                           " http_status=?, sent_at=?, transaction_id=?,"
                           " error_text=? WHERE id=?",
                           ("submitted" if ok else "failed",
                            _json.dumps({"add": r["player_id"],
                                         "drop": r["drop_player_id"],
                                         "bid": r["league_bid"]}),
                            200 if ok else None,
                            int(_time.time() * 1000),
                            (data or {}).get("transaction_id") if data else None,
                            None if ok else raw[:500], r["id"]))
            except Exception as e:
                cx.execute("UPDATE claims SET status='failed', error_text=?"
                           " WHERE id=?", (str(e)[:500], r["id"]))
            cx.commit()
    finally:
        for _r in rows:
            _SEND_INFLIGHT.discard(_r["id"])
    return {"processed": len(rows)}


@app.post("/api/claims/{cid}/retry")
def claim_retry(cid: int, week: int = 0):
    cx = connect()
    r = cx.execute("SELECT week FROM claims WHERE id=?", (cid,)).fetchone()
    if not r:
        err("claim no existe", 404)
    cx.execute("UPDATE claims SET status='ready', error_text=NULL WHERE id=?",
               (cid,))
    cx.commit()
    return claims_submit(week or int(r["week"]))


@app.post("/api/claims/{cid}/abandon")
def claim_abandon(cid: int):
    """Olvida un claim atascado (cancelado fuera de la herramienta, tid
    huérfano). Solo DB local, sin tocar Sleeper. Solo ready/submitted."""
    cx = connect()
    r = cx.execute("SELECT status FROM claims WHERE id=?", (cid,)).fetchone()
    if not r:
        err("claim no existe", 404)
    if r["status"] not in ("ready", "submitted"):
        err(f"estado {r['status']} no abandonable", 409)
    cx.execute("UPDATE claims SET status='failed',"
               " error_text='abandonado manual (tid huerfano)' WHERE id=?",
               (cid,))
    cx.commit()
    return {"ok": True}


class CancelBody(BaseModel):
    league_id: str
    transaction_id: str
    leg: int = 1


@app.post("/api/pending/cancel")
def pending_cancel(body: CancelBody):
    """Writer: NO probar en vivo sin aprobacion. Al cancelar en Sleeper,
    la fila local se marca failed para que el union de availability no
    la siga mostrando como waiver pendiente."""
    ok, raw = priv.cancel_claim(body.league_id, body.transaction_id, leg=body.leg)
    priv.bust_pending(body.league_id)
    pub.bust_bundle_display(body.league_id)
    if ok:
        cx = connect()
        cx.execute("UPDATE claims SET status='failed',"
                   " error_text='cancelado en Sleeper'"
                   " WHERE league_id=? AND transaction_id=?"
                   " AND status='submitted'",
                   (body.league_id, body.transaction_id))
        cx.commit()
    return {"ok": ok, "raw": raw[:500]}


class UpdateBid(BaseModel):
    league_id: str
    transaction_id: str
    bid: int = 0
    leg: int = 1


@app.post("/api/pending/update")
def pending_update(body: UpdateBid):
    """Edita bid/priority in-place (mismo tid, no duplica).
    Writer: NO probar en vivo sin aprobacion."""
    if body.bid < 0:
        err("bid invalido", 422)
    wm = pub.waiver_mode(body.league_id)
    if body.bid < int(wm.get("bid_min") or 0):
        err(f"bid {body.bid} < minimo {wm.get('bid_min')}", 422)
    ok, data, raw = priv.update_claim(
        body.league_id, body.transaction_id, {"waiver_bid": body.bid},
        leg=body.leg)
    priv.bust_pending(body.league_id)
    pub.bust_bundle_display(body.league_id)
    if ok:
        # El bid editado es el real en Sleeper: sincronizarlo o el digest
        # muestra el bid de creación (caso Winston $0 vs bid real).
        cx = connect()
        cx.execute("UPDATE claims SET league_bid=? WHERE league_id=?"
                   " AND transaction_id=? AND status='submitted'",
                   (int(body.bid), body.league_id, body.transaction_id))
        cx.commit()
    return {"ok": ok,
            "settings": (data or {}).get("settings") if data else None,
            "raw": None if ok else raw[:500]}


class ReorderBody(BaseModel):
    league_id: str
    ordered_ids: list[str] = []
    leg: int = 1


@app.post("/api/pending/reorder")
def pending_reorder(body: ReorderBody):
    """Reordena priority de claims (ligas claim). Una mutación por claim,
    secuencial con jitter — igual que lo hace la UI de Sleeper.
    Writer: NO probar en vivo sin aprobacion."""
    import random as _random
    import time as _time
    results = []
    for i, tid in enumerate(body.ordered_ids):
        _time.sleep(2.0 + _random.random() * 1.5)
        ok, data, raw = priv.update_claim(
            body.league_id, tid, {"priority": i}, leg=body.leg)
        # `raw` es el cuerpo crudo de la mutación: nunca se muestra. El
        # frontend pinta `error`; `dev` queda para support.
        results.append({"transaction_id": tid, "priority": i, "ok": ok,
                        "error": None if ok else "Sleeper no aceptó el "
                                                 "cambio de orden.",
                        "code": None if ok else E_SLEEPER_WRITE,
                        "dev": None if ok else raw[:300]})
        if not ok:
            break  # parar al primer fallo: el orden quedó parcial
    priv.bust_pending(body.league_id)
    pub.bust_bundle_display(body.league_id)
    return {"results": results}


@app.get("/api/export/{kind}")
def export_csv(kind: str, week: int = 0):
    import csv as _csv
    import io as _io
    from fastapi.responses import PlainTextResponse
    rows = claims_list(week, "ready" if kind == "ready" else
                       "submitted" if kind == "submitted" else "")
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["player_id", "league_id", "bid", "drop", "status"])
    for r in rows:
        w.writerow([r["player_id"], r["league_id"], r["league_bid"],
                    r["drop_player_id"], r["status"]])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


@app.post("/api/digest")
def digest(body: dict):
    from core.digest import run_digest
    week = int(body.get("week") or current_week_default())
    return run_digest(week, connect())


def _digest_rows(cx, wk):
    """Filas de claims de la semana con nombres cruzados (local)."""
    rows = [dict(r) for r in cx.execute(
        "SELECT * FROM claims WHERE week=? AND status != 'draft'"
        " ORDER BY league_id, id", (wk,))]
    pmap = pub.get_players_map()
    out = []
    for d in rows:
        info = pmap.get(str(d["player_id"]), {})
        dp = d.get("drop_player_id")
        out.append({
            "id": d["id"], "league_id": d["league_id"],
            "player_id": d["player_id"],
            "name": info.get("name", d["player_id"]),
            "pos": info.get("pos"), "team": info.get("team"),
            "league_bid": d["league_bid"],
            "drop_player_id": dp or "",
            "drop_name": (pmap.get(str(dp), {}).get("name", dp) if dp else ""),
            "status": d["status"], "resolution": d.get("resolution"),
            "transaction_id": d.get("transaction_id"),
            "sent_at": d.get("sent_at"), "resolved_at": d.get("resolved_at"),
            "checked_at": d.get("checked_at"),
            "error_text": nota_para_usuario(d.get("error_text")),
            "note": nota_para_usuario(d.get("note")), "winner": d.get("winner"),
        })
    return out


@app.get("/api/digest")
def digest_view(week: int = 0):
    """Morning-after digest: qué pasó con los claims de la semana.
    100% lectura local (DB + mapa en memoria): cero llamadas a Sleeper.
    week=0 → última semana con claims (el default placeholder no se usa:
    los claims guardan la semana real).
    El mapeo se declara verbatim: nuestra sem N se concilia contra
    Sleeper W{N} + W{N-1} (igual que verify)."""
    cx = connect()
    latest = cx.execute(
        "SELECT MAX(week) FROM claims WHERE status != 'draft'").fetchone()[0]
    latest = int(latest or current_week_default())
    # Default: la semana con más claims (donde está la acción), no la
    # última etiqueta (las etiquetas pueden venir de flujos viejos).
    top = cx.execute(
        "SELECT week FROM claims WHERE status != 'draft'"
        " GROUP BY week ORDER BY COUNT(*) DESC, week DESC LIMIT 1").fetchone()
    wk = int(week or (top[0] if top else latest))
    out = _digest_rows(cx, wk)
    unverified = sum(1 for d in out
                     if d["status"] == "submitted" and not d["checked_at"])
    return {"week": wk, "latest_week": latest,
            "sleeper_weeks": [wk, wk - 1],
            "unverified": unverified, "rows": out}


@app.get("/api/history")
def history_view():
    """Historial nuestro (DB local, cero Sleeper): todas las semanas con
    claims, nuevas primero. Opt-in desde el Hub (default: pending +
    resueltos de la semana)."""
    cx = connect()
    weeks = [r[0] for r in cx.execute(
        "SELECT DISTINCT week FROM claims WHERE status != 'draft'"
        " ORDER BY week DESC").fetchall()]
    return {"weeks": [{"week": int(w), "rows": _digest_rows(cx, int(w))}
                      for w in weeks]}


# ---------- FantasyPros wire (ETL consenso ECR, solo-lectura externa) ----------

@app.post("/api/fp-wire/import")
def fp_wire_import():
    """Baja el waiver-wire overall de FantasyPros y lo cruza con el mapa
    local de Sleeper. SOS se ignora (ruido). Solo escribe fp_wire local."""
    from core import fantasypros as fp
    try:
        return fp.run_import(connect(), pub.get_players_map())
    except Exception as e:
        fail(E_FP, "No pude leer la página de FantasyPros.", e)


@app.get("/api/fp-wire")
def fp_wire_list():
    """Snapshot importado, orden ECR. Sin import previo -> lista vacia."""
    cx = connect()
    rows = [dict(r) for r in cx.execute(
        "SELECT * FROM fp_wire ORDER BY rank_ecr")]
    meta = cx.execute(
        "SELECT COUNT(*), MAX(week), MAX(scoring), MAX(fetched_at)"
        " FROM fp_wire").fetchone()
    return {"week": meta[1] or 0, "scoring": meta[2] or "",
            "fetched_at": meta[3], "count": int(meta[0]), "rows": rows}


# ---------- SPA fallback (prod: UI + API en :3001) ----------
# Registrado AL FINAL: solo alcanza lo que /api/* y /docs no matchearon.

if os.path.isfile(_DIST_INDEX):
    @app.get("/{path:path}")
    def spa(path: str):
        """Archivos de web/dist, o index.html (router del lado cliente).
        /api/* jamás cae aquí (rutas registradas antes); si llega un
        /api fantasma se responde 404 JSON, no HTML."""
        if path.startswith("api"):
            err("no existe", 404)
        if path:
            cand = os.path.normpath(os.path.join(_DIST, path))
            if cand.startswith(_DIST) and os.path.isfile(cand):
                return FileResponse(cand)
        return FileResponse(_DIST_INDEX, headers={"Cache-Control": "no-store"})
