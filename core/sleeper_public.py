"""Capa publica solo -lectura (api.sleeper.app). Sin auth."""

import os
import time

from .fetch import sleeper_get

BASE = "https://api.sleeper.app/v1"

# /projections NO cuelga de api.sleeper.app/v1: ese host responde otra
# forma (lista corta sin player_id) y el parse daba 0 filas. La UI usa el
# dominio .com plano, que devuelve las ~3300 filas de la semana.
PROJ_BASE = "https://api.sleeper.com"


def get_user(username):
    """Ficha pública por username (para resolver user_id sin auth).
    Retorna {} si no existe."""
    r = sleeper_get(f"{BASE}/user/{username}", timeout=20)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    return r.json() or {}


_PROJ_REST_CACHE: dict = {}  # (season, week) -> (epoch_s, {pid: {...}})
PROJ_REST_TTL = 6 * 3600  # espejo del TTL que tenía el batch privado

_SCORING_CACHE: dict = {}  # ("scoring", league_id) -> (epoch_s, {stat: w})
SCORING_TTL = 24 * 3600


_LEAGUE_CACHE: dict = {}  # league_id -> (epoch_s, {league json})
_LEAGUE_TTL = 6 * 3600  # el /league trae settings + scoring_settings juntos


def get_league_full(league_id, fresh=False):
    """El JSON completo de la liga (cache 6h). Trae `settings` (waiver_day,
    waiver_clear_days...) y `scoring_settings` de UNA llamada: antes
    hacíamos dos GET al mismo endpoint."""
    import time as _time
    key = str(league_id)
    now = _time.time()
    hit = _LEAGUE_CACHE.get(key)
    if hit and not fresh and now - hit[0] < _LEAGUE_TTL:
        return hit[1]
    r = sleeper_get(f"{BASE}/league/{league_id}", timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    j = r.json() or {}
    _LEAGUE_CACHE[key] = (now, j)
    return j


def get_scoring_settings(league_id, fresh=False):
    """scoring_settings de la liga (CLAVE TOP-LEVEL, no dentro de settings).
    Es lo que usa la UI para pasar los pts proyectados a puntos de tu
    liga (NUCLEAR: 2.0/rec, 0.05/yd, 6/TD, 0.5/cmp, 1.0/rush att).
    Cache 24h: los settings cambian muy rare veces; fresh=1 lo revienta."""
    import time as _time
    key = ("scoring", str(league_id))
    now = _time.time()
    hit = _SCORING_CACHE.get(key)
    if hit and not fresh and now - hit[0] < SCORING_TTL:
        return hit[1]
    sc = get_league_full(league_id, fresh=fresh).get("scoring_settings") or {}
    _SCORING_CACHE[key] = (now, sc)
    return sc


def get_projections_week(season, week):
    """Proyecciones semanales vía REST pública (la que usa la propia UI).
    Sin JWT, una llamada (~3300 jugadores), cache 6h. Retorna {pid:
    {proj_ppr, proj_half, proj_std, opp}}. Lanza si Sleeper falla: el
    caller decide (la pestaña Proy falla fuerte, nunca degrada a rank
    en silencio). solo  se cachea lo no-vacío: un fetch malo jamás
    envenena las 6h siguientes."""
    import time as _time
    key = (str(season), int(week))
    now = _time.time()
    hit = _PROJ_REST_CACHE.get(key)
    if hit and now - hit[0] < PROJ_REST_TTL:
        return hit[1]
    # Params EXACTOS de la UI (HAR): requests codifica [] como %5B%5D y
    # Sleeper lee cero posiciones. Query literal, filtro en local.
    # OJO el host: /projections NO vive bajo /v1. Con /v1 la API responde
    # 200 con otra forma (lista de 7630 items sin player_id) y el parse
    # daba 0 filas. El host plano es el que usa la UI (3305 filas).
    pos = "&".join(f"position[]={p}" for p in
                   ["DEF", "FLEX", "K", "QB", "RB", "TE", "WR"])
    url = (f"{PROJ_BASE}/projections/nfl/{season}/{int(week)}"
           f"?season_type=regular&{pos}&order_by=ppr")
    r = sleeper_get(url, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    data = r.json()
    rows = data if isinstance(data, list) else (data or {}).get("rows") or []
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("player_id") or "")
        if not pid:
            continue
        st = row.get("stats") or {}
        out[pid] = {"proj_ppr": st.get("pts_ppr"),
                    "proj_half": st.get("pts_half_ppr"),
                    "proj_std": st.get("pts_std"),
                    # Stats crudas: el `pts_ppr` del payload es scoring
                    # estándar, no PPR. Los puntos de fantasy los calcula
                    # core/scoring.py desde aquí (ver nota de scoring).
                    "stats": st,
                    "opp": row.get("opponent")}
    if out:
        _PROJ_REST_CACHE[key] = (now, out)
    return out


def get_leagues(user_id, season):
    r = sleeper_get(f"{BASE}/user/{user_id}/leagues/nfl/{season}", timeout=20)
    r.raise_for_status()
    return r.json()


def get_rosters(league_id):
    r = sleeper_get(f"{BASE}/league/{league_id}/rosters", timeout=20)
    r.raise_for_status()
    return r.json()


def get_transactions(league_id, week):
    r = sleeper_get(f"{BASE}/league/{league_id}/transactions/{int(week)}",
                    timeout=20)
    r.raise_for_status()
    return r.json() or []


def get_league(league_id):
    r = sleeper_get(f"{BASE}/league/{league_id}", timeout=20)
    r.raise_for_status()
    return r.json()


def get_league_bundle(league_id, user_id):
    """Un par de llamadas (league + rosters) reutilizable por todo lo que
    una ruta necesite de una liga: modo, presupuesto, roster, rostered,
    roster_id, slots. Sin esto cada helper refetcheaba lo mismo (5 calls
    por _league_row, 6 por quick).

    SIEMPRE en vivo a propósito: availability, los writers y los re-checks
    pre-send dependen de que esto NO se cachee. Para pintar la lista de
    ligas está `get_bundle_display()`."""
    lg = get_league(league_id)
    rosters = get_rosters(league_id)
    mine = None
    for r in rosters:
        if str(r.get("owner_id")) == str(user_id):
            mine = r
            break
    return {"league": lg, "rosters": rosters, "roster": mine}


# Cache solo  de display para /api/leagues (record, saldo, slots libres,
# modo, semana). TTL corto porque los rosters cambian con cada waiver; los
# campos que deciden (availability, envío, clears) nunca leen de aquí.
_BUNDLE_DISPLAY: dict = {}
_BUNDLE_DISPLAY_TTL = 600  # 10 min


def get_bundle_display(league_id, user_id, fresh=False):
    """Bundle cacheado para pintar. `fresh=True` lo salta (el ↻ global)."""
    if not fresh:
        hit = _BUNDLE_DISPLAY.get(league_id)
        if hit and (time.time() - hit[0]) < _BUNDLE_DISPLAY_TTL:
            return hit[1]
    b = get_league_bundle(league_id, user_id)
    _BUNDLE_DISPLAY[league_id] = (time.time(), b)
    return b


def bust_bundle_display(league_id=None):
    """Lo vacían los writers: tras enviar/cancelar, el roster y el saldo de
    esa liga ya cambiaron y el cache de display quedó viejo."""
    if league_id is None:
        _BUNDLE_DISPLAY.clear()
    else:
        _BUNDLE_DISPLAY.pop(league_id, None)


def cache_stats():
    """Telegrafía del cache de display, para /api/health."""
    return {"display_entries": len(_BUNDLE_DISPLAY),
            "display_ttl_s": _BUNDLE_DISPLAY_TTL}


def clear_process_caches():
    """Vacía los memos de proceso. Los llama la API cuando cambia la
    cuenta de Sleeper: son claves por league_id, no por cuenta, así que
    un cambio de usuario deja datos del anterior en memoria hasta que
    expire el TTL."""
    _BUNDLE_DISPLAY.clear()
    _ROSTER_ID_MEMO.clear()


# roster_id es estable toda la temporada (solo  cambia si sales/entras de
# la liga): memo de proceso, evita un get_rosters por refresh.
_ROSTER_ID_MEMO: dict = {}


WAIVER_TZ_DEFAULT = "America/New_York"


WAIVER_TZ_DEFAULT = "UTC"


def _waiver_tz():
    """Marco en el que cuenta el día y la hora de waivers.

    UTC, no el reloj del usuario ni una zona inventada. Calibrado con un
    dato concreto: en México (CST, UTC-6, sin DST) los waivers corren a
    la 1:00 AM del miércoles, que es 07:00 UTC. 23:24 del martes + 97 min
    = 07:00 UTC del miércoles: el momento exacto. Con el reloj local la
    app contaba el día corrido y decía "libre" a las 00:01 mientras la
    liga seguía bloqueada.

    Override por .env `WAIVERS_WAIVER_TZ` si una liga corre con otro marco.
    """
    import datetime as _dt
    name = os.environ.get("WAIVERS_WAIVER_TZ", "").strip() or WAIVER_TZ_DEFAULT
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return _dt.timezone.utc


def _pacific_offset(at=None):
    """Offset de US/Pacific a ese instante: -7 con DST, -8 sin ella.

    El DST de USA corre del segundo domingo de marzo al primer domingo de
    noviembre, y no es el de Mexico: Mexico no hace DST, USA si. Por eso
    esto se resuelve con la zona de verdad y no con una constante.
    """
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        la = ZoneInfo("America/Los_Angeles")
        when = at or _dt.datetime.now(_dt.timezone.utc)
        off = when.astimezone(la).utcoffset()
        # utcoffset va con SIGNO: PDT es -7. Se devuelve tal cual para que
        # `utc = hora_pacifico - offset` sume (0 - (-7) = 07:00).
        return int(off.total_seconds() // 3600) if off else -8
    except Exception:
        # Sin tzdata: la mitad del año va -7, la otra -8. Ante duda, -8
        # (horario estándar) y lo absorbe la gracia del borde.
        return -7 if 3 <= _dt.datetime.now().month <= 10 else -8


def _process_hour(settings, at=None):
    """Hora de proceso de la liga en UTC.

    `settings.daily_waivers_hour` está en **hora PACÍFICA**, no en UTC ni
    en la del usuario. Verificado contra tres controles independientes:

      Juantasy  campo 0 -> 07:00 UTC = 1 AM CST  ("Clear Waivers:
                Wednesday (1 AM CST)" en el UI de Sleeper)
      Chopped   campo 8 -> 15:00 UTC = 9 AM CST  (el usuario: "9am")
      Dynasty   campo 4 -> 11:00 UTC = 5 AM CST  ("Processing Time
                5 AM CST" en el UI de Sleeper)

    Por eso en 0.1.37, que usaba un 07:00 UTC fijo, daba bien en Juantasy y
    NUCLEAR (ambas valen 0) por casualidad y mal en Chopped y Dynasty.
    """
    try:
        h = int((settings or {}).get("daily_waivers_hour", 0) or 0)
    except (TypeError, ValueError):
        h = 0
    return (h - _pacific_offset(at)) % 24


def waiver_window(settings: dict, league_id=None, now_ms: int = None):
    """Ventana de waivers de la LIGA: ¿el pool está bloqueado?

    Qué SÍ sabemos de Sleeper, y qué no (verificado con introspección del
    GraphQL y con un HAR de la UI de Sleeper):

    - NO existe un status por jugador. `LeaguePlayer` tiene 4 campos
      (`metadata`, `settings`, `player_id`, `league_id`) y de 244 queries
      del schema ninguna habla de waivers.
    - `LeaguePlayer.settings.waiver_clears_at` es un REGISTRO de cuándo
      resolvió cada waiver (en el HAR, 104 valores, todos en el pasado),
      no un calendario. solo  sirve para el jugador con un waiver vivo.
    - `/stats/nfl/{season}/{week}` viene VACÍO (la propia UI de Sleeper
      recibió 2 bytes), así que "jugó la semana pasada" no se puede leer.

    Lo que sí queda es la regla de la liga, y está en sus settings:
    `waiver_day_of_week` (día que procesa) y `waiver_clear_days` (cuántos
    días dura el lock). Con eso: el pool queda bloqueado desde que se juega
    hasta que la liga procesa, y libre durante los `waiver_clear_days`
    días siguientes. Eso es lo que la UI de Sleeper calcula por su cuenta
    para pintar el "W Wed" del pool entero.

    Se aplica por LIGA y no por jugador: durante la ventana no hay + para
    nadie, porque no sabemos quiénmitter. Cuando el jugador además trae
    `clears_at` propio, manda ese (Achane: lo dropearon hoy y resuelve en
    dos días, no el miércoles como el resto).

    Devuelve {locked, unknown, until_ms, day, reason}. Ante duda
    (`unknown`, settings sin día de waivers) NO se afirma que esté libre:
    devuelve unknown y la ruta decide."""
    import datetime as dt
    import time as _t
    s = settings or {}
    now = now_ms if now_ms is not None else int(_t.time() * 1000)

    def _int(k, d=0):
        # OJO: NO `s.get(k, d) or d`. En Python `0 or 2` da 2, asi que un
        # setting que vale 0 de verdad (Chopped trae waiver_clear_days=0)
        # se leia como el default. Un `or` asi se come los ceros.
        v = s.get(k)
        if v is None or v == "":
            return d
        try:
            return int(v)
        except (TypeError, ValueError):
            return d

    try:
        border_h = int(os.environ.get("WAIVER_BORDER_H", "6") or 6)
    except ValueError:
        border_h = 6
    try:
        grace_min = int(os.environ.get("WAIVER_GRACE_MIN", "30") or 30)
    except ValueError:
        grace_min = 30

    # Sin los settings NO hay regla. Se dice "no se" en vez de inventar:
    # inventar fue lo queFxjo que devuelva "libre" y ofrecía el +.
    if not s or s.get("waiver_day_of_week") is None:
        return {"locked": False, "unknown": True, "until_ms": 0, "day": None,
                "reason": "no se pudo leer el día de waivers de la liga"}

        # Hora de proceso: la de la liga, en Pacifico -> UTC (ver _process_hour).
    # Los dias que corre: en diaria la mascara, en semanal el dia de
    # "Clear Waivers" (que es `waiver_day_of_week`, y coincide con lo que
    # el UI de Sleeper llama "Clear Waivers: <dia> (<hora>)").
    hour = _process_hour(s, at=dt.datetime.fromtimestamp(
        now / 1000, dt.timezone.utc))
    day = _int("waiver_day_of_week", 0)   # 0=lunes (esta liga trae 2 y la
                                         # UI de Sleeper muestra "W Wed")

    tz = _waiver_tz()
    now_dt = dt.datetime.fromtimestamp(now / 1000, tz)
    today = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    offset = now_dt.weekday()          # lunes=0 ... domingo=6
    grace = dt.timedelta(minutes=max(0, grace_min))
    clear_days = _int("waiver_clear_days", 2)

    def _ms(d):
        return int(d.timestamp() * 1000)

    def _run_at(d, h=hour):
        return d.replace(hour=h % 24, minute=0, second=0, microsecond=0)

    if _int("daily_waivers"):
        mask = _int("daily_waivers_days", 0)
        process_days = {b for b in range(7) if mask & (1 << b)} or set(range(7))
    else:
        process_days = {day}

    # Último proceso YA OCURRIDO (más gracia), no el de esta semana a
    # ciegas: a las 04:23 UTC del miércoles, el de las 07:00 todavía no
    # había pasado y tomarlo como "último" liberaba el pool antes de que
    # Sleeper procesara. Ese era el bug de Juantasy.
    last = None
    for back in range(0, 15):
        d = today - dt.timedelta(days=back)
        if d.weekday() in process_days:
            m = _run_at(d)
            if m + grace <= now_dt:
                last = m
                break
    if last is None:                       # no deberia pasar: nunca encontro
        return {"locked": True, "unknown": False, "until_ms": 0, "day": day,
                "reason": "no se pudo ubicar el último proceso"}   # proceso

    free_from = last + grace
    free_until = free_from + dt.timedelta(days=clear_days)
    nxt = None
    for fwd in range(0, 15):
        d = today + dt.timedelta(days=fwd)
        if d.weekday() in process_days:
            m = _run_at(d)
            if m > now_dt:
                nxt = m
                break
    if nxt is None:
        nxt = last + dt.timedelta(days=7)
    # `waiver_clear_days == 0` NO se interpreta como "sin ventana libre": el
    # resto de la app lo lee como "resuelve el mismo día" (ver la cadena
    # `cadence` en _roster_row). No se inventa semántica acá: si una liga
    # no abre free agency, se declara abajo con WAIVERS_NEVER_FREE.
    #
    # Override por liga: hay ligas que NUNCA abren free agency aunque los
    # settings digan que tienen ventana. Dynasty Juantasy es una: su UI
    # muestra "Custom Daily Waivers: Monday … Sunday (5 AM CST)", o sea
    # corre TODOS los días, pero `daily_waivers_days` viene como máscara
    # [lun, mie, vie, dom] y no coincide. Cuando la API y la UI se
    # contradicen, gana la UI (es lo que el usuario ve) y queda anotado.
    # Override por liga con `WAIVERS_NEVER_FREE=<id1,id2>` en .env.
    never = os.environ.get("WAIVERS_NEVER_FREE", "") or ""
    ids = {x.strip() for x in never.split(",") if x.strip()}
    if league_id and str(league_id) in ids:
        return {"locked": True, "unknown": False,
                "until_ms": _ms(nxt if nxt is not None
                                else last + dt.timedelta(days=1)),
                "day": day,
                "reason": "esta liga no abre free agency"}

    if now_dt < free_until:
        if free_until - now_dt <= dt.timedelta(hours=border_h):
            # Cerca del corte se bloquea: el error caro es al revés
            # (ofrecer el add en ventana), el barato es "reclamá".
            return {"locked": True, "unknown": False, "until_ms": _ms(nxt),
                    "day": day,
                    "reason": "borde del corte de waivers: se reclama"}
        return {"locked": False, "unknown": False, "until_ms": 0,
                "day": None, "reason": ""}
    return {"locked": True, "unknown": False, "until_ms": _ms(nxt),
            "day": day,
            "reason": "post-juego: se reclama, no se agrega"}


def waiver_mode(league_id, _league=None):
    """Detecta FAAB vs claim normal desde settings publicos.
    waiver_type==2 -> FAAB (validado con tu Juantasy).
    0/1 -> claim por prioridad (sin bid).
    Retorna dict {mode, waiver_type, budget, bid_min}.
    _league: json ya fetcheado (bundle) para no repetir el GET."""
    lg = _league if _league is not None else get_league(league_id)
    s = lg.get("settings", {}) or {}
    wt = int(s.get("waiver_type", 0))
    if wt == 2:
        mode = "faab"
    else:
        mode = "claim"
    return {
        "mode": mode,
        "waiver_type": wt,
        "league_name": lg.get("name"),
        "budget": s.get("waiver_budget"),
        "bid_min": s.get("waiver_bid_min", 0),
    }


def remaining_budget(league_id, user_id, _league=None, _roster=None):
    lg = _league if _league is not None else get_league(league_id)
    budget = (lg.get("settings", {}) or {}).get("waiver_budget", 0)
    roster = _roster if _roster is not None else get_my_roster(league_id, user_id)
    used = ((roster or {}).get("settings", {}) or {}).get("waiver_budget_used", 0)
    return int(budget or 0) - int(used or 0)


def find_roster_id(league_id, user_id, _rosters=None):
    """Resuelve roster_id (el '7' hardcodeado) para no repetirlo a mano.
    Memo de proceso: estable toda la temporada."""
    key = (str(league_id), str(user_id))
    if key in _ROSTER_ID_MEMO:
        return _ROSTER_ID_MEMO[key]
    rosters = _rosters if _rosters is not None else get_rosters(league_id)
    rid = None
    for roster in rosters:
        if str(roster.get("owner_id")) == str(user_id):
            rid = int(roster["roster_id"])
            break
    if rid is not None:
        _ROSTER_ID_MEMO[key] = rid
    return rid


def roster_space(league, roster):
    """Slots libres reales, UNA sola fórmula en todo el repo.
    Taxi/IR viven dentro de players[] pero ocupan sus propios slots:
    se descuentan (acotados a su capacidad; el excedente come banca).
    Puro, sin red. Caso extremo real: Dynasty 25/29/taxi-2/IR-3 -> free 1.
    """
    positions = (league or {}).get("roster_positions") or []
    settings = (league or {}).get("settings") or {}
    players = (roster or {}).get("players") or []
    taxi = (roster or {}).get("taxi") or []
    reserve = (roster or {}).get("reserve") or []
    taxi_slots = int(settings.get("taxi_slots") or 0)
    res_slots = int(settings.get("reserve_slots") or 0)
    used = (len(players) - min(len(taxi), taxi_slots)
            - min(len(reserve), res_slots))
    return {"capacity": len(positions),
            "used": max(0, used),
            "free": max(0, len(positions) - used),
            "taxi_capacity": taxi_slots,
            "taxi_used": len(taxi),
            "taxi_free": max(0, taxi_slots - len(taxi)),
            "reserve_capacity": res_slots,
            "reserve_used": len(reserve),
            "reserve_free": max(0, res_slots - len(reserve))}


def get_my_roster(league_id, user_id, _rosters=None):
    rosters = _rosters if _rosters is not None else get_rosters(league_id)
    for roster in rosters:
        if str(roster.get("owner_id")) == str(user_id):
            return roster
    return None


def get_rostered_ids(league_id, _rosters=None):
    """Todos los player_ids con dueño en la liga. El add NO debe estar aqui."""
    taken = set()
    for roster in (_rosters if _rosters is not None else get_rosters(league_id)):
        for pid in (roster.get("players") or []):
            taken.add(str(pid))
    return taken


_MAP_CACHE: dict = {}  # cache_path -> (mtime_ns, data)

# Miembros de la liga: user_id -> display_name. Cambia poco; TTL largo.
# solo  se usa al resolver (ganador de un lost) — 1 call/liga/día.
_USERS_CACHE: dict = {}  # league_id -> (epoch_s, {user_id: name})
USERS_TTL = 24 * 3600


def get_league_users(league_id):
    """Mapa user_id -> display_name de la liga (para nombrar al ganador
    de un lost). Cache 24h en memoria."""
    import time as _time
    now = _time.time()
    key = str(league_id)
    hit = _USERS_CACHE.get(key)
    if hit and now - hit[0] < USERS_TTL:
        return hit[1]
    r = sleeper_get(f"{BASE}/league/{league_id}/users", timeout=20)
    r.raise_for_status()
    m = {str(u.get("user_id")): (u.get("display_name") or u.get("username")
                                 or str(u.get("user_id")))
         for u in (r.json() or [])}
    _USERS_CACHE[key] = (now, m)
    return m


def _mtime(path):
    import os as _os
    try:
        return _os.path.getmtime(path)
    except OSError:
        return -1


def _players_cache_path():
    """Cache del mapa de jugadores (~10MB) en la carpeta de DATOS, no en
    la de instalación: sobrevive reinstalls y no ensucia el program dir.
    WAIVERS_DATA_DIR (Tauri) o repo (dev)."""
    data = os.environ.get("WAIVERS_DATA_DIR", "")
    if not data:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data = root
    return os.path.join(data, "players_nfl.json")


def get_players_map(cache_path=None):
    """Mapa player_id -> {name, pos, team, rank, inj}. Cache local (API pesa ~10MB).
    rank = search_rank de Sleeper (su propio orden de lista).
    inj = injury_status (Q/O/IR/...) para matizar el rank anual.
    Segunda capa: memo en memoria por mtime - antes se re-parseaba el
    JSON de 10MB en CADA llamada (claims_list lo pedía 5x por refresh,
    search una vez por tecla)."""
    import json
    import os
    cache_path = cache_path or _players_cache_path()
    if (cache_path in _MAP_CACHE
            and _MAP_CACHE[cache_path][0] == _mtime(cache_path)):
        return _MAP_CACHE[cache_path][1]
    # Cache corrupta (escritura a medias, proceso muerto en el `json.dump`)
    # NO puede voltear la app: se trata como cache miss y se re-fetch.
    # Antes el `json.load` saltaba sin try y un archivo a medias dejaba
    # /free-agents y el buscador caidos para siempre, en todas las ligas.
    if os.path.exists(cache_path):
        cached = None
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
        except (OSError, ValueError):
            cached = None
        # Cache vieja sin rank+inj -> refetch una vez y listo.
        sample = next(iter((cached or {}).values()), {})
        if cached and "rank" in sample and "inj" in sample:
            _MAP_CACHE[cache_path] = (_mtime(cache_path), cached)
            return cached
        # Refresh falló antes y nos queda una version previa utilizable: se
        # sirve con el nombre/rank viejos en vez de tumbar la pantalla.
        if cached:
            _MAP_CACHE[cache_path] = (_mtime(cache_path), cached)
            return cached
    r = sleeper_get(f"{BASE}/players/nfl", timeout=60)
    r.raise_for_status()
    raw = r.json()
    slim = {}
    for pid, p in raw.items():
        name = (p.get("full_name")
                or (str(p.get("first_name", "")) + " " + str(p.get("last_name", ""))).strip())
        if not name:
            continue
        slim[str(pid)] = {
            "name": name,
            "pos": p.get("position"),
            "team": p.get("team"),
            "rank": p.get("search_rank"),
            "inj": p.get("injury_status"),
        }
    # Escritura ATOMICA: dump a .tmp y os.replace. Escribir en el sitio
    # deja el archivo truncado si el proceso muere a mitad, que es como
    # se fabricaba el cache corrupto de arriba.
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(slim, f)
    os.replace(tmp, cache_path)
    _MAP_CACHE[cache_path] = (_mtime(cache_path), slim)
    return slim


def search_players(query, limit=10, cache_path="players_nfl.json"):
    q = query.lower()
    m = get_players_map(cache_path)
    hits = [(pid, v) for pid, v in m.items() if q in v["name"].lower()]
    return hits[:limit]
